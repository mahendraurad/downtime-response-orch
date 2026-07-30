import React, { useState } from 'react';
import { useAuth } from '../context/AuthContext';
import { loginUser } from '../api/auth';

const ROLE_LABELS = {
  admin:       'System Admin',
  plant_ops:   'Plant Operations',
  leadership:  'Leadership',
  safety:      'Safety',
  engineering: 'Engineering',
};

const PERSONA_LABELS = {
  supervisor:  'Plant Supervisor',
  engineer:    'Reliability Engineer',
  maintenance: 'Maintenance Planner',
  manager:     'Plant Manager',
  executive:   'VP Operations',
  md:          'Managing Director',
  ot:          'OT / Controls',
  safety:      'Safety Officer',
};

export default function LoginScreen() {
  const { login } = useAuth();
  const [username, setUsername] = useState('');
  const [password, setPassword] = useState('');
  const [error, setError]       = useState('');
  const [loading, setLoading]   = useState(false);

  async function handleSubmit(e) {
    e.preventDefault();
    if (!username.trim() || !password) return;
    setError('');
    setLoading(true);
    try {
      const data = await loginUser(username.trim(), password);
      login(data);
    } catch (err) {
      setError(err.message || 'Login failed. Please try again.');
    } finally {
      setLoading(false);
    }
  }

  return (
    <div style={styles.overlay}>
      <div style={styles.card}>

        {/* Header */}
        <div style={styles.header}>
          <div style={styles.logo}>DRO</div>
          <div style={styles.title}>Downtime Response Orchestrator</div>
          <div style={styles.subtitle}>Sign in to access your workspace</div>
        </div>

        {/* Form */}
        <form onSubmit={handleSubmit} style={styles.form}>
          <div style={styles.field}>
            <label style={styles.label}>Username</label>
            <input
              style={styles.input}
              type="text"
              value={username}
              onChange={e => setUsername(e.target.value)}
              placeholder="e.g. john_ops"
              autoComplete="username"
              autoFocus
              disabled={loading}
            />
          </div>

          <div style={styles.field}>
            <label style={styles.label}>Password</label>
            <input
              style={styles.input}
              type="password"
              value={password}
              onChange={e => setPassword(e.target.value)}
              placeholder="••••••••"
              autoComplete="current-password"
              disabled={loading}
            />
          </div>

          {error && <div style={styles.error}>{error}</div>}

          <button
            type="submit"
            style={{ ...styles.btn, opacity: loading ? 0.6 : 1 }}
            disabled={loading || !username.trim() || !password}
          >
            {loading ? 'Signing in…' : 'Sign In'}
          </button>
        </form>

        {/* Demo accounts hint */}
        <div style={styles.hint}>
          <div style={styles.hintTitle}>Demo accounts</div>
          <div style={styles.hintGrid}>
            {[
              { u: 'admin',      p: 'admin123', role: 'admin' },
              { u: 'john_ops',   p: 'ops123',   role: 'plant_ops' },
              { u: 'sarah_mgr',  p: 'mgr123',   role: 'leadership' },
              { u: 'raj_safety', p: 'saf123',   role: 'safety' },
            ].map(({ u, p, role }) => (
              <button
                key={u}
                style={styles.demoBtn}
                onClick={() => { setUsername(u); setPassword(p); }}
                disabled={loading}
                type="button"
              >
                <span style={styles.demoUser}>{u}</span>
                <span style={styles.demoRole}>{ROLE_LABELS[role]}</span>
              </button>
            ))}
          </div>
        </div>

        {/* Role-to-persona reference */}
        <details style={styles.details}>
          <summary style={styles.summary}>Roles &amp; persona access</summary>
          <div style={styles.roleTable}>
            {[
              { role: 'admin',       personas: ['supervisor','engineer','maintenance','manager','executive','md','ot','safety'] },
              { role: 'plant_ops',   personas: ['supervisor','engineer','maintenance','ot'] },
              { role: 'leadership',  personas: ['manager','executive','md'] },
              { role: 'safety',      personas: ['safety','supervisor'] },
              { role: 'engineering', personas: ['engineer','ot'] },
            ].map(({ role, personas }) => (
              <div key={role} style={styles.roleRow}>
                <span style={styles.roleTag}>{ROLE_LABELS[role]}</span>
                <span style={styles.personaList}>
                  {personas.map(p => PERSONA_LABELS[p] || p).join(' · ')}
                </span>
              </div>
            ))}
          </div>
        </details>

      </div>
    </div>
  );
}

const styles = {
  overlay: {
    display: 'flex',
    alignItems: 'center',
    justifyContent: 'center',
    minHeight: '100vh',
    background: 'var(--bg)',
    padding: '24px',
  },
  card: {
    width: '100%',
    maxWidth: '420px',
    background: 'var(--bg2)',
    border: '1px solid var(--b)',
    borderRadius: '12px',
    padding: '32px 28px 24px',
    display: 'flex',
    flexDirection: 'column',
    gap: '24px',
  },
  header: { textAlign: 'center' },
  logo: {
    display: 'inline-block',
    background: 'var(--ac)',
    color: '#fff',
    fontFamily: 'var(--m)',
    fontWeight: 700,
    fontSize: '13px',
    letterSpacing: '2px',
    padding: '6px 14px',
    borderRadius: '6px',
    marginBottom: '12px',
  },
  title: {
    color: 'var(--t)',
    fontFamily: 'var(--f)',
    fontWeight: 600,
    fontSize: '17px',
    marginBottom: '4px',
  },
  subtitle: {
    color: 'var(--t2)',
    fontFamily: 'var(--f)',
    fontSize: '13px',
  },
  form: { display: 'flex', flexDirection: 'column', gap: '14px' },
  field: { display: 'flex', flexDirection: 'column', gap: '6px' },
  label: {
    color: 'var(--t2)',
    fontFamily: 'var(--f)',
    fontSize: '12px',
    fontWeight: 500,
    letterSpacing: '.04em',
    textTransform: 'uppercase',
  },
  input: {
    background: 'var(--sf)',
    border: '1px solid var(--b)',
    borderRadius: '7px',
    color: 'var(--t)',
    fontFamily: 'var(--m)',
    fontSize: '13px',
    padding: '9px 12px',
    outline: 'none',
    width: '100%',
  },
  error: {
    background: 'var(--rdm)',
    border: '1px solid var(--rd)',
    borderRadius: '6px',
    color: 'var(--rd)',
    fontFamily: 'var(--f)',
    fontSize: '12px',
    padding: '8px 12px',
  },
  btn: {
    background: 'var(--ac)',
    border: 'none',
    borderRadius: '7px',
    color: '#fff',
    cursor: 'pointer',
    fontFamily: 'var(--f)',
    fontSize: '14px',
    fontWeight: 600,
    padding: '10px',
    marginTop: '2px',
    transition: 'opacity .15s',
  },
  hint: {
    borderTop: '1px solid var(--b)',
    paddingTop: '16px',
  },
  hintTitle: {
    color: 'var(--t2)',
    fontFamily: 'var(--f)',
    fontSize: '11px',
    fontWeight: 500,
    letterSpacing: '.06em',
    textTransform: 'uppercase',
    marginBottom: '10px',
  },
  hintGrid: {
    display: 'grid',
    gridTemplateColumns: '1fr 1fr',
    gap: '6px',
  },
  demoBtn: {
    background: 'var(--sf)',
    border: '1px solid var(--b)',
    borderRadius: '7px',
    cursor: 'pointer',
    display: 'flex',
    flexDirection: 'column',
    gap: '2px',
    padding: '8px 10px',
    textAlign: 'left',
    transition: 'border-color .15s',
  },
  demoUser: {
    color: 'var(--ac)',
    fontFamily: 'var(--m)',
    fontSize: '12px',
    fontWeight: 600,
  },
  demoRole: {
    color: 'var(--t2)',
    fontFamily: 'var(--f)',
    fontSize: '11px',
  },
  details: {
    borderTop: '1px solid var(--b)',
    paddingTop: '14px',
  },
  summary: {
    color: 'var(--t2)',
    cursor: 'pointer',
    fontFamily: 'var(--f)',
    fontSize: '12px',
    userSelect: 'none',
  },
  roleTable: {
    display: 'flex',
    flexDirection: 'column',
    gap: '6px',
    marginTop: '10px',
  },
  roleRow: {
    display: 'flex',
    alignItems: 'baseline',
    gap: '8px',
    flexWrap: 'wrap',
  },
  roleTag: {
    background: 'var(--ag)',
    border: '1px solid var(--b)',
    borderRadius: '4px',
    color: 'var(--ac)',
    fontFamily: 'var(--m)',
    fontSize: '10px',
    fontWeight: 600,
    padding: '2px 6px',
    whiteSpace: 'nowrap',
  },
  personaList: {
    color: 'var(--t2)',
    fontFamily: 'var(--f)',
    fontSize: '11px',
  },
};
