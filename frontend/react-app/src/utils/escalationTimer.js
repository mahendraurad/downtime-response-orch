export function remainingSeconds(escalatesAtUtc, nowMs = Date.now()) {
  const deadlineMs = Date.parse(escalatesAtUtc || '');
  if (!Number.isFinite(deadlineMs)) return null;
  return Math.max(0, Math.ceil((deadlineMs - nowMs) / 1000));
}

export function formatCountdown(totalSeconds) {
  if (totalSeconds == null) return '--:--:--';
  const seconds = Math.max(0, Math.floor(totalSeconds));
  const hours = Math.floor(seconds / 3600);
  const minutes = Math.floor((seconds % 3600) / 60);
  const remainder = seconds % 60;
  return [hours, minutes, remainder]
    .map(value => String(value).padStart(2, '0'))
    .join(':');
}

export function formatEscalationTime(escalatesAtUtc, locale) {
  const deadline = new Date(escalatesAtUtc || '');
  if (Number.isNaN(deadline.getTime())) return 'deadline unavailable';
  return new Intl.DateTimeFormat(locale, {
    year: 'numeric', month: 'short', day: '2-digit',
    hour: '2-digit', minute: '2-digit', second: '2-digit',
    timeZoneName: 'short',
  }).format(deadline);
}

export function activeEscalationStep(escalation, stageIndex = 0) {
  if (escalation?.status !== 'active' || !Array.isArray(escalation.steps)) {
    return null;
  }
  return escalation.steps[stageIndex] || null;
}
