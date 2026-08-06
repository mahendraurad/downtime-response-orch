/**
 * Persona presentation and work-order task projection.
 *
 * Persona identity and role focus are stable UI configuration. Operational
 * task facts are always projected from the current /api/workorders response;
 * asset IDs, work-order IDs, dates, parts, crew and condition values do not
 * live in this file.
 */

export const PD = {
  supervisor: {
    nm: 'Plant Supervisor — James Kowalski',
    ds: 'Operational decisions, run/defer risk, shift management and escalation',
    av: '🦺', bg: 'var(--odm)', col: 'var(--or)',
  },
  engineer: {
    nm: 'Reliability Engineer — Priya Nair',
    ds: 'Fault diagnostics, RUL modelling, root cause and technical validation',
    av: '🔧', bg: 'var(--ag)', col: 'var(--ac)',
  },
  maintenance: {
    nm: 'Maintenance Planner — Tom Rodriguez',
    ds: 'Scheduling, parts availability, crew allocation and repair sequencing',
    av: '🛠', bg: 'var(--gd)', col: 'var(--gn)',
  },
  manager: {
    nm: 'Plant Manager — Sarah Chen',
    ds: 'Production impact, cost trade-offs, OEE and line-level risk decisions',
    av: '📊', bg: 'var(--pdm)', col: 'var(--pu)',
  },
  executive: {
    nm: 'VP Operations — Michael Osei',
    ds: 'Portfolio risk, reliability KPIs, financial returns and strategic oversight',
    av: '👔', bg: 'var(--adm)', col: 'var(--am)',
  },
  ot: {
    nm: 'OT / Controls — Raj Iyer',
    ds: 'Signal health, historian integrity, sensor validation and CMMS integration',
    av: '📡', bg: 'var(--rdm)', col: 'var(--rd)',
  },
  safety: {
    nm: 'Safety Officer — Lisa Park',
    ds: 'LOTO, permits, PPE, crew qualifications and regulatory compliance',
    av: '🛡', bg: 'var(--tdm)', col: 'var(--tl)',
  },
};

const PRIORITY_RANK = { CRITICAL: 0, HIGH: 1, MEDIUM: 2, LOW: 3 };
const TERMINAL_STATUSES = new Set(['closed', 'completed', 'cancelled', 'canceled', 'rejected']);

const TASK_POLICY = {
  supervisor: {
    instruction: 'Review the operational run/defer decision and required shift action',
    label: 'Operational decision', icon: '⚡',
    col: 'rgba(255,77,106,.1)', bc: 'rgba(255,77,106,.35)', tc: 'var(--rd)',
  },
  engineer: {
    instruction: 'Validate the diagnosis, evidence and RUL basis before execution',
    label: 'Technical validation', icon: '🔬',
    col: 'rgba(79,142,255,.12)', bc: 'rgba(79,142,255,.35)', tc: 'var(--ac2)',
  },
  maintenance: {
    instruction: 'Confirm the schedule, parts, crew and incomplete planning steps',
    label: 'Execution planning', icon: '📦',
    col: 'rgba(46,204,138,.1)', bc: 'rgba(46,204,138,.35)', tc: 'var(--gn)',
  },
  manager: {
    instruction: 'Review the production and financial trade-off and decide whether authority is sufficient',
    label: 'Management decision', icon: '📈',
    col: 'rgba(167,139,250,.1)', bc: 'rgba(167,139,250,.35)', tc: 'var(--pu)',
  },
  executive: {
    instruction: 'Assess this work order in the portfolio risk and investment context',
    label: 'Portfolio oversight', icon: '🌐',
    col: 'rgba(255,183,64,.1)', bc: 'rgba(255,183,64,.35)', tc: 'var(--am)',
  },
  ot: {
    instruction: 'Validate signal, historian, sensor and integration prerequisites for this work',
    label: 'Data integrity', icon: '📡',
    col: 'rgba(255,77,106,.1)', bc: 'rgba(255,77,106,.35)', tc: 'var(--rd)',
  },
  safety: {
    instruction: 'Validate the safety checklist, isolation requirements and qualified personnel before work',
    label: 'Safety clearance', icon: '🛡',
    col: 'rgba(45,212,191,.1)', bc: 'rgba(45,212,191,.3)', tc: 'var(--tl)',
  },
};

function value(source, ...keys) {
  for (const key of keys) {
    const candidate = source?.[key];
    if (candidate !== undefined && candidate !== null && candidate !== '') return candidate;
  }
  return '';
}

function normaliseWorkOrder(source) {
  const checklist = value(source, 'cl', 'checklist') || [];
  return {
    id: String(value(source, 'id')),
    asset: String(value(source, 'as', 'asset_display', 'asset_id')),
    title: String(value(source, 'ti', 'title', 'description')),
    priority: String(value(source, 'pr', 'priority') || 'MEDIUM').toUpperCase(),
    status: String(value(source, 'st', 'status') || 'Pending'),
    due: String(value(source, 'due')),
    assignedTo: String(value(source, 'asgn', 'assigned_to')),
    parts: String(value(source, 'parts')),
    duration: String(value(source, 'est', 'est_hours')),
    rul: String(value(source, 'rul')),
    risk: String(value(source, 'risk')),
    cost: String(value(source, 'cost')),
    type: String(value(source, 'tp', 'type')),
    checklist: checklist.map(item => ({
      text: String(value(item, 'tx', 'text')),
      tag: String(value(item, 'tg', 'tag')),
      done: Boolean(value(item, 'ck', 'done')),
    })),
  };
}

function hasChecklistTag(workOrder, pattern) {
  return workOrder.checklist.some(item => pattern.test(`${item.tag} ${item.text}`));
}

function isRelevant(persona, workOrder) {
  const searchable = `${workOrder.type} ${workOrder.title}`;
  if (persona === 'executive' || persona === 'manager') {
    return workOrder.priority === 'CRITICAL' || workOrder.priority === 'HIGH';
  }
  if (persona === 'engineer') {
    return /condition|predict|bearing|fault|inspect/i.test(searchable)
      || hasChecklistTag(workOrder, /evidence|qa|diagnos/i);
  }
  if (persona === 'ot') {
    return /instrument|sensor|signal|controls|data/i.test(searchable)
      || hasChecklistTag(workOrder, /ot|data|signal|sensor|historian|qa/i);
  }
  if (persona === 'safety') {
    return hasChecklistTag(workOrder, /safety|loto|permit|isolation|ppe/i);
  }
  return true;
}

function sortWorkOrders(workOrders) {
  return workOrders.slice().sort((left, right) => {
    const priority = (PRIORITY_RANK[left.priority] ?? 9) - (PRIORITY_RANK[right.priority] ?? 9);
    if (priority !== 0) return priority;
    return left.id.localeCompare(right.id);
  });
}

function pendingItems(workOrder, pattern = null) {
  return workOrder.checklist
    .filter(item => !item.done && (!pattern || pattern.test(`${item.tag} ${item.text}`)))
    .map(item => item.text)
    .filter(Boolean);
}

function taskFacts(persona, workOrder) {
  const facts = [
    `work order ${workOrder.id}`,
    workOrder.asset && `asset ${workOrder.asset}`,
    workOrder.title && `scope ${workOrder.title}`,
    `status ${workOrder.status}`,
    `priority ${workOrder.priority}`,
    workOrder.due && `deadline ${workOrder.due}`,
  ].filter(Boolean);

  if (persona === 'supervisor' || persona === 'engineer') {
    if (workOrder.risk) facts.push(`risk ${workOrder.risk}`);
    if (workOrder.rul) facts.push(`RUL ${workOrder.rul}`);
  }
  if (persona === 'maintenance') {
    if (workOrder.parts) facts.push(`parts ${workOrder.parts}`);
    if (workOrder.assignedTo) facts.push(`assigned crew ${workOrder.assignedTo}`);
    if (workOrder.duration) facts.push(`estimated duration ${workOrder.duration}`);
    const pending = pendingItems(workOrder).slice(0, 3);
    if (pending.length) facts.push(`pending steps ${pending.join('; ')}`);
  }
  if (persona === 'manager' || persona === 'executive') {
    if (workOrder.risk) facts.push(`risk ${workOrder.risk}`);
    if (workOrder.cost) facts.push(`recorded work-order cost ${workOrder.cost}`);
  }
  if (persona === 'ot') {
    const pending = pendingItems(workOrder, /ot|data|signal|sensor|historian|qa|evidence/i).slice(0, 3);
    if (pending.length) facts.push(`pending controls/data steps ${pending.join('; ')}`);
  }
  if (persona === 'safety') {
    const safety = workOrder.checklist
      .filter(item => /safety|loto|permit|isolation|ppe/i.test(`${item.tag} ${item.text}`))
      .map(item => `${item.text} (${item.done ? 'complete' : 'pending'})`)
      .slice(0, 3);
    if (safety.length) facts.push(`safety steps ${safety.join('; ')}`);
  }
  return facts;
}

function buildTask(persona, workOrder) {
  const policy = TASK_POLICY[persona];
  return {
    icon: policy.icon,
    lbl: `${policy.label} · ${workOrder.priority}`,
    nm: workOrder.title || workOrder.id,
    col: policy.col, bc: policy.bc, tc: policy.tc,
    workOrderId: workOrder.id,
    assetId: workOrder.asset,
    q: `${policy.instruction}. Use the current backend record: ${taskFacts(persona, workOrder).join(' | ')}.`,
  };
}

function escapeHtml(valueToEscape) {
  return String(valueToEscape).replace(/[&<>"']/g, character => ({
    '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;',
  }[character]));
}

/** Project current backend work orders into role-appropriate persona tasks. */
export function resolvePersonaData(persona, fleetSummary, workOrders = []) {
  const source = PD[persona];
  if (!source) return source;
  const active = sortWorkOrders(
    workOrders.map(normaliseWorkOrder).filter(order => (
      order.id && !TERMINAL_STATUSES.has(order.status.toLowerCase())
    )),
  );
  const relevant = active.filter(order => isRelevant(persona, order));
  const selected = (relevant.length ? relevant : active).slice(0, 6);
  const actions = selected.map(order => buildTask(persona, order));
  const firstName = source.nm.split(' — ')[1]?.split(' ')[0] || source.nm;
  const fleetTotal = fleetSummary?.total ?? 0;

  let greeting = `Hello ${escapeHtml(firstName)}. No active work orders are currently available from the backend. Persona tasks will appear when work-order records are returned.`;
  if (active.length) {
    const top = active[0];
    const due = top.due ? `, due ${escapeHtml(top.due)}` : '';
    greeting = `Hello ${escapeHtml(firstName)}. <strong style="color:var(--t)">${active.length} active work orders</strong> are linked to the current ${fleetTotal}-asset fleet. Highest priority: <strong style="color:var(--t)">${escapeHtml(top.id)} for ${escapeHtml(top.asset)}</strong> — ${escapeHtml(top.priority)} / ${escapeHtml(top.status)}${due}. The tasks below are derived from current backend work-order records.`;
  }

  const tags = selected.slice(0, 3).map(order => (
    `${order.id} · ${order.asset || 'unassigned asset'} · ${order.status}`
  ));
  if (!tags.length) tags.push(`${fleetTotal} assets · 0 active work orders`);

  return {
    ...source,
    gr: greeting,
    actions,
    qp: actions.slice(0, 4).map(action => action.q),
    tags,
  };
}
