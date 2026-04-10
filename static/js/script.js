/* ═══════════════════════════════════════════════════════
   MedTrack — Frontend JavaScript
═══════════════════════════════════════════════════════ */

// ─── DOM helpers ────────────────────────────────────────
const $ = (sel, ctx = document) => ctx.querySelector(sel);
const $$ = (sel, ctx = document) => [...ctx.querySelectorAll(sel)];

// ─── Sidebar toggle (mobile) ─────────────────────────────
function initSidebar() {
  const hamburger = $('#hamburger');
  const sidebar   = $('.sidebar');
  const overlay   = $('#sidebar-overlay');
  if (!hamburger) return;
  hamburger.addEventListener('click', () => {
    sidebar.classList.toggle('open');
    overlay && overlay.classList.toggle('show');
  });
  overlay && overlay.addEventListener('click', () => {
    sidebar.classList.remove('open');
    overlay.classList.remove('show');
  });
}

// ─── Flash auto-dismiss ──────────────────────────────────
function initFlash() {
  $$('.flash-msg').forEach(el => {
    setTimeout(() => {
      el.style.opacity = '0';
      el.style.transform = 'translateY(-6px)';
      el.style.transition = 'all .4s ease';
      setTimeout(() => el.remove(), 400);
    }, 3500);
  });
}

// ─── Progress bar animation ───────────────────────────────
function animateProgress() {
  $$('.progress-fill').forEach(bar => {
    const target = bar.dataset.pct || bar.style.width;
    bar.style.width = '0';
    requestAnimationFrame(() => {
      requestAnimationFrame(() => {
        bar.style.width = target;
      });
    });
  });
}

// ─── Adherence ring (SVG) ────────────────────────────────
function drawAdherenceRing(pct) {
  const canvas = $('#adherence-ring');
  if (!canvas) return;
  const r  = 54;
  const cx = 70;
  const cy = 70;
  const circumference = 2 * Math.PI * r;
  const dashOffset = circumference - (pct / 100) * circumference;

  canvas.innerHTML = `
    <svg width="140" height="140" viewBox="0 0 140 140">
      <circle cx="${cx}" cy="${cy}" r="${r}"
        fill="none" stroke="#e2e8f0" stroke-width="12"/>
      <circle cx="${cx}" cy="${cy}" r="${r}"
        fill="none" stroke="#00c9a7" stroke-width="12"
        stroke-linecap="round"
        stroke-dasharray="${circumference}"
        stroke-dashoffset="${circumference}"
        transform="rotate(-90 ${cx} ${cy})"
        id="ring-progress"
        style="transition: stroke-dashoffset 1.2s cubic-bezier(.4,0,.2,1)"/>
    </svg>`;

  setTimeout(() => {
    const ring = $('#ring-progress');
    if (ring) ring.style.strokeDashoffset = dashOffset;
  }, 100);
}

// ─── Confirm delete ──────────────────────────────────────
function initDeleteConfirm() {
  $$('.btn-delete').forEach(btn => {
    btn.addEventListener('click', function(e) {
      if (!confirm('Are you sure you want to delete this medication?')) {
        e.preventDefault();
      }
    });
  });
}

// ─── Form validation ─────────────────────────────────────
function initFormValidation() {
  $$('form[data-validate]').forEach(form => {
    form.addEventListener('submit', function(e) {
      let valid = true;
      $$('[required]', form).forEach(field => {
        field.classList.remove('error');
        if (!field.value.trim()) {
          field.classList.add('error');
          field.style.borderColor = 'var(--coral)';
          valid = false;
        } else {
          field.style.borderColor = '';
        }
      });
      if (!valid) {
        e.preventDefault();
        shakeForm(form);
      } else {
        showLoading();
      }
    });
  });
}

function shakeForm(el) {
  el.style.animation = 'none';
  el.offsetHeight; // reflow
  el.style.animation = 'shake .4s ease';
}

// ─── Loading overlay ─────────────────────────────────────
function showLoading() {
  const lo = $('#loading-overlay');
  if (lo) lo.classList.add('show');
}
function hideLoading() {
  const lo = $('#loading-overlay');
  if (lo) lo.classList.remove('show');
}

// ─── Live stats refresh (every 60s on dashboard) ─────────
function startStatRefresh() {
  if (!document.body.classList.contains('dashboard-page')) return;
  setInterval(async () => {
    try {
      const res  = await fetch('/api/stats');
      const data = await res.json();
      const el = id => document.getElementById(id);
      if (el('stat-total'))   el('stat-total').textContent   = data.total;
      if (el('stat-taken'))   el('stat-taken').textContent   = data.taken;
      if (el('stat-missed'))  el('stat-missed').textContent  = data.missed;
      if (el('stat-pending')) el('stat-pending').textContent = data.pending;
      if (el('pct-text'))     el('pct-text').textContent     = data.pct + '%';
      drawAdherenceRing(data.pct);
    } catch (_) {}
  }, 60_000);
}

// ─── Time display on topbar ───────────────────────────────
function startClock() {
  const el = $('#topbar-time');
  if (!el) return;
  const fmt = () => {
    const now = new Date();
    el.textContent = now.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });
  };
  fmt();
  setInterval(fmt, 30_000);
}

// ─── CSS shake keyframe (injected once) ──────────────────
(function injectShake() {
  const style = document.createElement('style');
  style.textContent = `
    @keyframes shake {
      0%,100%{ transform:translateX(0) }
      25%{ transform:translateX(-6px) }
      75%{ transform:translateX(6px) }
    }
    .form-control.error{ border-color:var(--coral)!important; }
  `;
  document.head.appendChild(style);
})();

// ─── Mark taken button loading state ─────────────────────
function initMarkTaken() {
  $$('.btn-mark-taken').forEach(btn => {
    btn.closest('form')?.addEventListener('submit', () => {
      btn.innerHTML = '<span class="spinner" style="width:16px;height:16px;border-width:2px"></span>';
      btn.disabled = true;
    });
  });
}

// ─── Tooltip on stat cards ────────────────────────────────
function initTooltips() {
  $$('[data-tip]').forEach(el => {
    el.style.position = 'relative';
    el.addEventListener('mouseenter', function() {
      const tip = document.createElement('div');
      tip.className = '_tooltip';
      tip.textContent = this.dataset.tip;
      Object.assign(tip.style, {
        position:'absolute', bottom:'calc(100% + 8px)', left:'50%',
        transform:'translateX(-50%)',
        background:'var(--navy)', color:'#fff',
        padding:'6px 12px', borderRadius:'6px',
        fontSize:'12px', whiteSpace:'nowrap',
        zIndex:999, pointerEvents:'none',
        animation:'fadeUp .15s ease',
      });
      this.appendChild(tip);
    });
    el.addEventListener('mouseleave', function() {
      this.querySelector('._tooltip')?.remove();
    });
  });
}

// ─── Boot ────────────────────────────────────────────────
document.addEventListener('DOMContentLoaded', () => {
  initSidebar();
  initFlash();
  animateProgress();
  initDeleteConfirm();
  initFormValidation();
  initMarkTaken();
  initTooltips();
  startClock();
  startStatRefresh();

  // Draw ring if pct attr present
  const ringWrap = $('#adherence-ring');
  if (ringWrap) {
    drawAdherenceRing(parseInt(ringWrap.dataset.pct || '0', 10));
  }
});