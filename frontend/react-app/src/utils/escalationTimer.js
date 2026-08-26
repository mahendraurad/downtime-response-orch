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

function normalizedPersonaId(personaId) {
  return personaId === 'md' ? 'executive' : String(personaId || '');
}

export function activeEscalationStep(escalation, stageIndex = 0, personaId = '') {
  if (escalation?.status !== 'active' || !Array.isArray(escalation.steps)) {
    return null;
  }
  // The last target in the configured chain is the final authority. It has
  // nobody further to escalate to, so it must never receive a countdown even
  // if a manually forwarded/restored card carries an earlier stage index.
  const terminalPersonaId = escalation.steps.at(-1)?.to_persona_id;
  if (terminalPersonaId
      && normalizedPersonaId(personaId) === normalizedPersonaId(terminalPersonaId)) {
    return null;
  }
  return escalation.steps[stageIndex] || null;
}
