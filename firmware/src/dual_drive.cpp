#include <Arduino.h>

#include <cstdint>
#include <cstdio>

namespace {

// NodeMCU-32S / ESP32-WROOM-32 pin map. Both signal inputs are ADC1 channels,
// so they remain usable if Wi-Fi is added later. GPIO34..39 are input-only,
// which is exactly what the ADC and lead-off signals require.
constexpr uint8_t kSignalAPin = 36;  // SENSOR_VP / ADC1_CH0
constexpr uint8_t kShutdownAPin = 27;
constexpr uint8_t kLeadOffAPlusPin = 32;
constexpr uint8_t kLeadOffAMinusPin = 35;

constexpr uint8_t kSignalBPin = 39;  // SENSOR_VN / ADC1_CH3
constexpr uint8_t kShutdownBPin = 26;
constexpr uint8_t kLeadOffBPlusPin = 33;
constexpr uint8_t kLeadOffBMinusPin = 34;

constexpr uint32_t kBaudRate = 460800;
constexpr uint32_t kPairPeriodUs = 1000;    // 1,000 paired frames/second
constexpr uint32_t kMetaPeriodUs = 1000000; // one telemetry line/second
constexpr uint16_t kAdcMinimum = 0;
constexpr uint16_t kAdcMaximum = 4095;
constexpr uint16_t kClipMargin = 4;

struct SamplePair {
  uint16_t a;
  uint16_t b;
};

// A power-of-two queue absorbs short USB/UART stalls. If the host stops
// reading, acquisition stays on its time grid and tx_drop_total exposes loss.
constexpr uint16_t kQueueCapacity = 1024;
constexpr uint16_t kQueueMask = kQueueCapacity - 1;
static_assert((kQueueCapacity & kQueueMask) == 0,
              "Queue capacity must be a power of two");

SamplePair sample_queue[kQueueCapacity]{};
uint16_t queue_head = 0;
uint16_t queue_tail = 0;

uint32_t next_pair_us = 0;
uint32_t next_meta_us = 0;
uint32_t stats_started_us = 0;

uint64_t pair_total = 0;
uint64_t missed_deadline_total = 0;
uint64_t deadline_overrun_total = 0;
uint64_t tx_drop_total = 0;

uint32_t interval_pairs = 0;
uint32_t interval_a_clip_low = 0;
uint32_t interval_a_clip_high = 0;
uint32_t interval_b_clip_low = 0;
uint32_t interval_b_clip_high = 0;
uint32_t interval_max_lateness_us = 0;
uint32_t interval_max_acquire_us = 0;

// Metadata drains after every sample captured before its snapshot, preserving
// the ordering of the mixed CSV/metadata serial stream.
char meta_line[512]{};
size_t meta_length = 0;
size_t meta_offset = 0;
uint16_t meta_queue_barrier = 0;
bool meta_pending = false;

inline bool deadlineReached(uint32_t now, uint32_t deadline) {
  return static_cast<int32_t>(now - deadline) >= 0;
}

inline uint16_t queuedPairCount() {
  return static_cast<uint16_t>((queue_head - queue_tail) & kQueueMask);
}

bool enqueuePair(uint16_t a, uint16_t b) {
  const uint16_t next_head =
      static_cast<uint16_t>((queue_head + 1U) & kQueueMask);
  if (next_head == queue_tail) {
    ++tx_drop_total;
    return false;
  }

  sample_queue[queue_head] = {a, b};
  queue_head = next_head;
  return true;
}

size_t appendUnsigned(uint16_t value, char *output) {
  char reversed[4];
  size_t digits = 0;
  do {
    reversed[digits++] = static_cast<char>('0' + (value % 10U));
    value = static_cast<uint16_t>(value / 10U);
  } while (value != 0U);

  for (size_t index = 0; index < digits; ++index) {
    output[index] = reversed[digits - index - 1U];
  }
  return digits;
}

size_t encodePairLine(const SamplePair &pair, char *output) {
  size_t length = appendUnsigned(pair.a, output);
  output[length++] = ',';
  length += appendUnsigned(pair.b, output + length);
  output[length++] = '\n';
  return length;
}

void acquireDuePair(uint32_t now) {
  if (!deadlineReached(now, next_pair_us)) {
    return;
  }

  const uint32_t lateness_us = now - next_pair_us;
  if (lateness_us > interval_max_lateness_us) {
    interval_max_lateness_us = lateness_us;
  }

  // Sample once at the current instant instead of taking a stale catch-up
  // burst. Both conversions occur in one 1 ms frame and are emitted as a pair.
  const uint32_t skipped_slots = lateness_us / kPairPeriodUs;
  if (skipped_slots != 0U) {
    missed_deadline_total += skipped_slots;
    ++deadline_overrun_total;
  }
  next_pair_us += (skipped_slots + 1U) * kPairPeriodUs;

  const uint32_t acquire_started_us = micros();
  const uint16_t a = static_cast<uint16_t>(analogRead(kSignalAPin));
  const uint16_t b = static_cast<uint16_t>(analogRead(kSignalBPin));
  const uint32_t acquire_us = micros() - acquire_started_us;
  if (acquire_us > interval_max_acquire_us) {
    interval_max_acquire_us = acquire_us;
  }

  ++pair_total;
  ++interval_pairs;
  if (a <= (kAdcMinimum + kClipMargin)) {
    ++interval_a_clip_low;
  }
  if (a >= (kAdcMaximum - kClipMargin)) {
    ++interval_a_clip_high;
  }
  if (b <= (kAdcMinimum + kClipMargin)) {
    ++interval_b_clip_low;
  }
  if (b >= (kAdcMaximum - kClipMargin)) {
    ++interval_b_clip_high;
  }

  enqueuePair(a, b);
}

void prepareMetadata(uint32_t now) {
  if (meta_pending || !deadlineReached(now, next_meta_us)) {
    return;
  }

  const uint32_t elapsed_us = now - stats_started_us;
  const uint64_t rate_x100 =
      elapsed_us == 0U
          ? 0U
          : (static_cast<uint64_t>(interval_pairs) * 100000000ULL) /
                elapsed_us;
  const uint32_t rate_whole = static_cast<uint32_t>(rate_x100 / 100U);
  const uint32_t rate_fraction = static_cast<uint32_t>(rate_x100 % 100U);

  const uint8_t a_lo_plus = digitalRead(kLeadOffAPlusPin) == HIGH ? 1U : 0U;
  const uint8_t a_lo_minus = digitalRead(kLeadOffAMinusPin) == HIGH ? 1U : 0U;
  const uint8_t b_lo_plus = digitalRead(kLeadOffBPlusPin) == HIGH ? 1U : 0U;
  const uint8_t b_lo_minus = digitalRead(kLeadOffBMinusPin) == HIGH ? 1U : 0U;
  const uint8_t a_leads_off =
      (a_lo_plus != 0U || a_lo_minus != 0U) ? 1U : 0U;
  const uint8_t b_leads_off =
      (b_lo_plus != 0U || b_lo_minus != 0U) ? 1U : 0U;

  const int written = std::snprintf(
      meta_line, sizeof(meta_line),
      "#META,channels=2,rate_hz=%lu.%02lu,pairs=%lu,pair_total=%llu,"
      "sample_total=%llu,missed_total=%llu,overrun_total=%llu,"
      "max_late_us=%lu,max_acquire_us=%lu,tx_drop_total=%llu,"
      "a_lo_plus=%u,a_lo_minus=%u,a_leads_off=%u,a_clip_low=%lu,"
      "a_clip_high=%lu,b_lo_plus=%u,b_lo_minus=%u,b_leads_off=%u,"
      "b_clip_low=%lu,b_clip_high=%lu,queued=%u\n",
      static_cast<unsigned long>(rate_whole),
      static_cast<unsigned long>(rate_fraction),
      static_cast<unsigned long>(interval_pairs),
      static_cast<unsigned long long>(pair_total),
      static_cast<unsigned long long>(pair_total * 2ULL),
      static_cast<unsigned long long>(missed_deadline_total),
      static_cast<unsigned long long>(deadline_overrun_total),
      static_cast<unsigned long>(interval_max_lateness_us),
      static_cast<unsigned long>(interval_max_acquire_us),
      static_cast<unsigned long long>(tx_drop_total),
      static_cast<unsigned int>(a_lo_plus),
      static_cast<unsigned int>(a_lo_minus),
      static_cast<unsigned int>(a_leads_off),
      static_cast<unsigned long>(interval_a_clip_low),
      static_cast<unsigned long>(interval_a_clip_high),
      static_cast<unsigned int>(b_lo_plus),
      static_cast<unsigned int>(b_lo_minus),
      static_cast<unsigned int>(b_leads_off),
      static_cast<unsigned long>(interval_b_clip_low),
      static_cast<unsigned long>(interval_b_clip_high),
      static_cast<unsigned int>(queuedPairCount()));

  if (written > 0 && static_cast<size_t>(written) < sizeof(meta_line)) {
    meta_length = static_cast<size_t>(written);
    meta_offset = 0;
    meta_queue_barrier = queue_head;
    meta_pending = true;
  }

  interval_pairs = 0;
  interval_a_clip_low = 0;
  interval_a_clip_high = 0;
  interval_b_clip_low = 0;
  interval_b_clip_high = 0;
  interval_max_lateness_us = 0;
  interval_max_acquire_us = 0;
  stats_started_us = now;
  next_meta_us = now + kMetaPeriodUs;
}

void serviceSerialOutput() {
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

  char line[10]; // "4095,4095\n"
  const size_t length = encodePairLine(sample_queue[queue_tail], line);
  if (Serial.availableForWrite() < static_cast<int>(length)) {
    return;
  }

  const size_t sent = Serial.write(
      reinterpret_cast<const uint8_t *>(line), length);
  if (sent == length) {
    queue_tail = static_cast<uint16_t>((queue_tail + 1U) & kQueueMask);
  }
}

} // namespace

void setup() {
  pinMode(kShutdownAPin, OUTPUT);
  pinMode(kShutdownBPin, OUTPUT);
  digitalWrite(kShutdownAPin, HIGH);
  digitalWrite(kShutdownBPin, HIGH);

  pinMode(kSignalAPin, INPUT);
  pinMode(kSignalBPin, INPUT);
  pinMode(kLeadOffAPlusPin, INPUT);
  pinMode(kLeadOffAMinusPin, INPUT);
  pinMode(kLeadOffBPlusPin, INPUT);
  pinMode(kLeadOffBMinusPin, INPUT);

  analogReadResolution(12);
  analogSetPinAttenuation(kSignalAPin, ADC_11db);
  analogSetPinAttenuation(kSignalBPin, ADC_11db);

  Serial.setTxBufferSize(4096);
  Serial.begin(kBaudRate);

  const uint32_t started_us = micros();
  next_pair_us = started_us + kPairPeriodUs;
  stats_started_us = started_us;
  next_meta_us = started_us + kMetaPeriodUs;
}

void loop() {
  acquireDuePair(micros());
  prepareMetadata(micros());
  serviceSerialOutput();
}
