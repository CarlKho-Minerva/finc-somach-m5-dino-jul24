#include <Arduino.h>

#include <cstdint>
#include <cstdio>

namespace {

// Hardware is fixed to Carl's proven NodeMCU-32S wiring from the working
// attempt-2-WORKS / sensor-test sketches.  SDN is controlled by GPIO27 rather
// than tied directly to the 3V3 rail, so it must be asserted before sampling.
constexpr uint8_t kSignalPin = 36;  // AD8232 OUTPUT -> ADC1_CH0 (SENSOR_VP)
constexpr uint8_t kShutdownPin = 27;
constexpr uint8_t kLeadOffMinusPin = 35;
constexpr uint8_t kLeadOffPlusPin = 32;

constexpr uint32_t kBaudRate = 115200;
constexpr uint32_t kSamplePeriodUs = 1000;      // 1,000 samples/second
constexpr uint32_t kMetaPeriodUs = 1000000;     // one telemetry line/second
constexpr uint16_t kAdcMinimum = 0;
constexpr uint16_t kAdcMaximum = 4095;
constexpr uint16_t kClipMargin = 4;

// A power-of-two queue absorbs short USB/UART stalls without disturbing ADC
// deadlines. If the host stops reading for a long time, acquisition continues
// and tx_drop_total reports samples that could not be queued.
constexpr uint16_t kSampleQueueCapacity = 512;
constexpr uint16_t kSampleQueueMask = kSampleQueueCapacity - 1;
static_assert((kSampleQueueCapacity & kSampleQueueMask) == 0,
              "Sample queue capacity must be a power of two");

uint16_t sample_queue[kSampleQueueCapacity]{};
uint16_t queue_head = 0;
uint16_t queue_tail = 0;

uint32_t next_sample_us = 0;
uint32_t next_meta_us = 0;
uint32_t stats_started_us = 0;

uint64_t sample_total = 0;
uint64_t missed_deadline_total = 0;
uint64_t deadline_overrun_total = 0;
uint64_t tx_drop_total = 0;

uint32_t interval_samples = 0;
uint32_t interval_clip_low = 0;
uint32_t interval_clip_high = 0;
uint32_t interval_max_lateness_us = 0;

// Metadata is sent only after samples that preceded its snapshot. New samples
// may continue entering the queue while the metadata line drains in chunks.
char meta_line[320]{};
size_t meta_length = 0;
size_t meta_offset = 0;
uint16_t meta_queue_barrier = 0;
bool meta_pending = false;

// Signed subtraction is the standard wrap-safe comparison for micros() as long
// as deadlines are always less than 2^31 microseconds apart.
inline bool deadlineReached(uint32_t now, uint32_t deadline) {
  return static_cast<int32_t>(now - deadline) >= 0;
}

inline uint16_t queuedSampleCount() {
  return static_cast<uint16_t>((queue_head - queue_tail) & kSampleQueueMask);
}

bool enqueueSample(uint16_t value) {
  const uint16_t next_head =
      static_cast<uint16_t>((queue_head + 1U) & kSampleQueueMask);
  if (next_head == queue_tail) {
    ++tx_drop_total;
    return false;
  }

  sample_queue[queue_head] = value;
  queue_head = next_head;
  return true;
}

size_t encodeSampleLine(uint16_t value, char *output) {
  // ADC values are 0..4095, so this fixed-size integer conversion is faster and
  // more predictable than invoking printf 1,000 times per second.
  char reversed[4];
  size_t digits = 0;
  do {
    reversed[digits++] = static_cast<char>('0' + (value % 10U));
    value = static_cast<uint16_t>(value / 10U);
  } while (value != 0U);

  for (size_t index = 0; index < digits; ++index) {
    output[index] = reversed[digits - index - 1U];
  }
  output[digits] = '\n';
  return digits + 1U;
}

void acquireDueSample(uint32_t now) {
  if (!deadlineReached(now, next_sample_us)) {
    return;
  }

  const uint32_t lateness_us = now - next_sample_us;
  if (lateness_us > interval_max_lateness_us) {
    interval_max_lateness_us = lateness_us;
  }

  // Never take a burst of stale conversions to "catch up". Take one current
  // conversion, account for elapsed slots, and retain the original time grid.
  const uint32_t skipped_slots = lateness_us / kSamplePeriodUs;
  if (skipped_slots != 0U) {
    missed_deadline_total += skipped_slots;
    ++deadline_overrun_total;
  }
  next_sample_us += (skipped_slots + 1U) * kSamplePeriodUs;

  const uint16_t raw = static_cast<uint16_t>(analogRead(kSignalPin));
  ++sample_total;
  ++interval_samples;

  if (raw <= (kAdcMinimum + kClipMargin)) {
    ++interval_clip_low;
  }
  if (raw >= (kAdcMaximum - kClipMargin)) {
    ++interval_clip_high;
  }

  enqueueSample(raw);
}

void prepareMetadata(uint32_t now) {
  if (meta_pending || !deadlineReached(now, next_meta_us)) {
    return;
  }

  const uint32_t elapsed_us = now - stats_started_us;
  const uint64_t rate_x100 =
      elapsed_us == 0U
          ? 0U
          : (static_cast<uint64_t>(interval_samples) * 100000000ULL) /
                elapsed_us;
  const uint32_t rate_whole = static_cast<uint32_t>(rate_x100 / 100U);
  const uint32_t rate_fraction = static_cast<uint32_t>(rate_x100 % 100U);

  const uint8_t lo_plus = digitalRead(kLeadOffPlusPin) == HIGH ? 1U : 0U;
  const uint8_t lo_minus = digitalRead(kLeadOffMinusPin) == HIGH ? 1U : 0U;
  const uint8_t leads_off = (lo_plus != 0U || lo_minus != 0U) ? 1U : 0U;

  const int written = std::snprintf(
      meta_line, sizeof(meta_line),
      "#META,rate_hz=%lu.%02lu,samples=%lu,sample_total=%llu,"
      "missed_total=%llu,overrun_total=%llu,max_late_us=%lu,"
      "tx_drop_total=%llu,lo_plus=%u,lo_minus=%u,leads_off=%u,"
      "clip_low=%lu,clip_high=%lu,queued=%u\n",
      static_cast<unsigned long>(rate_whole),
      static_cast<unsigned long>(rate_fraction),
      static_cast<unsigned long>(interval_samples),
      static_cast<unsigned long long>(sample_total),
      static_cast<unsigned long long>(missed_deadline_total),
      static_cast<unsigned long long>(deadline_overrun_total),
      static_cast<unsigned long>(interval_max_lateness_us),
      static_cast<unsigned long long>(tx_drop_total),
      static_cast<unsigned int>(lo_plus),
      static_cast<unsigned int>(lo_minus),
      static_cast<unsigned int>(leads_off),
      static_cast<unsigned long>(interval_clip_low),
      static_cast<unsigned long>(interval_clip_high),
      static_cast<unsigned int>(queuedSampleCount()));

  if (written > 0 && static_cast<size_t>(written) < sizeof(meta_line)) {
    meta_length = static_cast<size_t>(written);
    meta_offset = 0;
    meta_queue_barrier = queue_head;
    meta_pending = true;
  }

  interval_samples = 0;
  interval_clip_low = 0;
  interval_clip_high = 0;
  interval_max_lateness_us = 0;
  stats_started_us = now;
  next_meta_us = now + kMetaPeriodUs;
}

void serviceSerialOutput() {
  // Preserve chronological placement: samples captured before the metadata
  // snapshot leave the queue first. Samples captured afterward wait behind it.
  if (meta_pending && queue_tail == meta_queue_barrier) {
    const int available = Serial.availableForWrite();
    if (available <= 0) {
      return;
    }

    const size_t remaining = meta_length - meta_offset;
    const size_t available_bytes = static_cast<size_t>(available);
    const size_t chunk =
        remaining < available_bytes ? remaining : available_bytes;
    const size_t sent = Serial.write(
        reinterpret_cast<const uint8_t *>(meta_line + meta_offset), chunk);
    meta_offset += sent;
    if (meta_offset == meta_length) {
      meta_pending = false;
      meta_length = 0;
      meta_offset = 0;
    }
    return;
  }

  if (queue_tail == queue_head) {
    return;
  }

  char line[5];
  const size_t length = encodeSampleLine(sample_queue[queue_tail], line);
  if (Serial.availableForWrite() < static_cast<int>(length)) {
    return;
  }

  const size_t sent =
      Serial.write(reinterpret_cast<const uint8_t *>(line), length);
  if (sent == length) {
    queue_tail = static_cast<uint16_t>((queue_tail + 1U) & kSampleQueueMask);
  }
}

}  // namespace

void setup() {
  pinMode(kShutdownPin, OUTPUT);
  digitalWrite(kShutdownPin, HIGH);  // HIGH = AD8232 normal operation
  pinMode(kSignalPin, INPUT);
  pinMode(kLeadOffMinusPin, INPUT);
  pinMode(kLeadOffPlusPin, INPUT);

  analogReadResolution(12);
  analogSetPinAttenuation(kSignalPin, ADC_11db);

  // A larger software TX buffer absorbs the once-per-second metadata burst.
  // All writes below still check availableForWrite() and never intentionally
  // block acquisition.
  Serial.setTxBufferSize(2048);
  Serial.begin(kBaudRate);

  const uint32_t started_us = micros();
  next_sample_us = started_us + kSamplePeriodUs;
  stats_started_us = started_us;
  next_meta_us = started_us + kMetaPeriodUs;
}

void loop() {
  acquireDueSample(micros());
  prepareMetadata(micros());
  serviceSerialOutput();
}
