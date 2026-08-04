(() => {
  'use strict';

  const BUILD = 'premium-store-split-v24.2';
  const tg = window.Telegram?.WebApp;

  const FALLBACK_PLANS = {
    account: [
      ['account_1m', '1 месяц', 30, 199, 'Без скидки', 0],
      ['account_4m', '4 месяца', 120, 756, 'Скидка 5%', 5],
      ['account_7m', '7 месяцев', 210, 1254, 'Скидка 10%', 10],
      ['account_10m', '10 месяцев', 300, 1692, 'Скидка 15%', 15],
      ['account_12m', '12 месяцев', 365, 1910, 'Скидка 20%', 20],
    ].map(([code, title, days, stars, badge, discount_percent]) => ({
      code,
      title,
      days,
      stars,
      badge,
      discount_percent,
      months: Number(code.match(/_(\d+)m$/)?.[1] || 1),
      scope: 'account',
      description: 'Действует на аккаунте и во всех беседах, где вы создатель.',
    })),
    group: [
      ['group_1m', '1 месяц', 30, 99, 'Без скидки', 0],
      ['group_4m', '4 месяца', 120, 376, 'Скидка 5%', 5],
      ['group_7m', '7 месяцев', 210, 624, 'Скидка 10%', 10],
      ['group_10m', '10 месяцев', 300, 842, 'Скидка 15%', 15],
      ['group_12m', '12 месяцев', 365, 950, 'Скидка 20%', 20],
    ].map(([code, title, days, stars, badge, discount_percent]) => ({
      code,
      title,
      days,
      stars,
      badge,
      discount_percent,
      months: Number(code.match(/_(\d+)m$/)?.[1] || 1),
      scope: 'group',
      description: 'Действует только в выбранной беседе.',
    })),
  };

  const state = {
    plans: FALLBACK_PLANS,
    accountStatus: null,
    groupStatus: null,
    renderingAccount: false,
    renderingGroup: false,
  };

  const escapeHtml = value => String(value ?? '')
    .replaceAll('&', '&amp;')
    .replaceAll('<', '&lt;')
    .replaceAll('>', '&gt;')
    .replaceAll('"', '&quot;')
    .replaceAll("'", '&#039;');

  function authHeaders(json = true) {
    const headers = {};
    if (json) headers['Content-Type'] = 'application/json';
    if (tg?.initData) headers['X-Telegram-Init-Data'] = tg.initData;

    const devUser = new URLSearchParams(location.search).get('dev_user_id');
    if (devUser) headers['X-Dev-User-Id'] = devUser;
    return headers;
  }

  async function request(url, options = {}) {
    const response = await fetch(url, {
      ...options,
      headers: {
        ...authHeaders(options.body !== undefined),
        ...(options.headers || {}),
      },
    });

    const text = await response.text();
    let data = null;
    try {
      data = text ? JSON.parse(text) : null;
    } catch (_) {
      data = text;
    }

    if (!response.ok) {
      const detail = data?.detail || data?.message || data || `HTTP ${response.status}`;
      throw new Error(String(detail));
    }
    return data;
  }

  function notify(message, error = false) {
    document.querySelector('[data-premium-purchase-toast]')?.remove();
    const toast = document.createElement('div');
    toast.dataset.premiumPurchaseToast = '1';
    toast.className = `premium-purchase-toast${error ? ' error' : ''}`;
    toast.textContent = message;
    document.body.appendChild(toast);
    requestAnimationFrame(() => toast.classList.add('visible'));
    setTimeout(() => toast.remove(), 3800);
    tg?.HapticFeedback?.notificationOccurred(error ? 'error' : 'success');
  }

  function currentChatId() {
    const raw = document.querySelector('#chat-select')?.value;
    const value = Number(raw);
    return Number.isFinite(value) && value !== 0 ? value : null;
  }

  function installStyles() {
    if (document.querySelector(`style[data-build="${BUILD}"]`)) return;
    const style = document.createElement('style');
    style.dataset.build = BUILD;
    style.textContent = `
      .shop-card[data-premium-shop="account"]{
        background:
          radial-gradient(circle at 86% 8%,rgba(243,195,79,.24),transparent 38%),
          linear-gradient(145deg,color-mix(in srgb,#f3c34f 9%,var(--secondary-bg)),var(--secondary-bg));
      }
      .shop-card[data-premium-shop="account"] .shop-card-icon{color:#f3c34f;background:color-mix(in srgb,#f3c34f 15%,transparent)}
      .shop-card[data-premium-shop="group"]{
        background:
          radial-gradient(circle at 86% 8%,color-mix(in srgb,var(--button) 26%,transparent),transparent 38%),
          linear-gradient(145deg,color-mix(in srgb,var(--button) 8%,var(--secondary-bg)),var(--secondary-bg));
      }
      .shop-card[data-premium-shop] strong{display:block;margin-top:auto;padding-top:12px;color:var(--button);font-size:14px}
      .premium-split-back{margin:0 0 12px;padding:8px 0;border:0;background:transparent;color:var(--button);font-weight:750}
      .premium-scope-note{margin:12px 0 14px;padding:13px 15px;border:1px solid color-mix(in srgb,var(--button) 25%,transparent);border-radius:16px;background:var(--premium-bg);color:var(--hint);font-size:13px;line-height:1.45}
      .premium-scope-note b{color:var(--text)}
      .premium-purchase-plan{position:relative;display:flex;min-height:100%;flex-direction:column}
      .premium-purchase-plan p{flex:1}
      .premium-purchase-plan .premium-saving{display:block;margin:-3px 0 10px;color:var(--button);font-size:12px;font-weight:800}
      .premium-purchase-plan .premium-scope-label{display:inline-flex;width:max-content;margin:0 0 9px;padding:4px 8px;border-radius:999px;background:var(--premium-bg);color:var(--button);font-size:10px;font-weight:850;letter-spacing:.04em}
      .premium-purchase-plan button[disabled]{opacity:.48;cursor:not-allowed}
      .premium-account-details,.premium-group-details{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:8px;margin:12px 0 0}
      .premium-account-details .premium-detail,.premium-group-details .premium-detail{padding:12px;border:1px solid var(--separator);border-radius:16px;background:var(--secondary-bg)}
      .premium-account-details small,.premium-group-details small{display:block;color:var(--hint);font-size:11px}
      .premium-account-details b,.premium-group-details b{display:block;margin-top:4px;overflow-wrap:anywhere}
      .premium-purchase-toast{position:fixed;left:50%;bottom:calc(94px + var(--safe-bottom,0px));z-index:99999;max-width:calc(100vw - 32px);padding:12px 16px;border:1px solid rgba(42,168,239,.38);border-radius:15px;background:#12354b;color:#fff;box-shadow:0 16px 44px rgba(0,0,0,.34);transform:translate(-50%,20px);opacity:0;transition:.2s ease;text-align:center;font-weight:700}
      .premium-purchase-toast.visible{transform:translate(-50%,0);opacity:1}
      .premium-purchase-toast.error{border-color:rgba(255,83,113,.48);background:#4a1e2a}
      @media(max-width:760px){.premium-account-details,.premium-group-details{grid-template-columns:1fr}.plans[data-premium-plans]{grid-template-columns:1fr}}
    `;
    document.head.appendChild(style);
  }

  function premiumCard(scope) {
    const isAccount = scope === 'account';
    const target = isAccount ? 'premium-account' : 'premium';
    const title = isAccount ? 'Premium аккаунта' : 'Premium группы';
    const description = isAccount
      ? 'Для аккаунта и всех бесед, где вы являетесь создателем.'
      : 'Отдельная подписка только для выбранной беседы.';
    const price = isAccount ? 'от 199 ⭐' : 'от 99 ⭐';
    return `
      <button class="shop-card premium nav pressable" data-view="${target}" data-premium-shop="${scope}" type="button">
        <span class="shop-card-icon"><svg><use href="#i-shop-premium"></use></svg></span>
        <h3>${title}</h3>
        <p>${description}</p>
        <strong>${price}</strong>
      </button>`;
  }

  function splitStoreCards() {
    const grid = document.querySelector('.shop-grid');
    if (!grid) return false;

    const accountCard = grid.querySelector('[data-premium-shop="account"]');
    const groupCard = grid.querySelector('[data-premium-shop="group"]');
    if (accountCard && groupCard) return true;

    const oldCard = [...grid.querySelectorAll('.shop-card')].find(card => {
      if (card.dataset.premiumShop) return false;
      return card.matches('[data-view="premium"]') || card.querySelector('h3')?.textContent?.trim() === 'Premium';
    });
    if (!oldCard) return false;

    const template = document.createElement('template');
    template.innerHTML = premiumCard('account') + premiumCard('group');
    oldCard.replaceWith(template.content);
    return true;
  }

  function ensureAccountPanel() {
    let panel = document.querySelector('[data-panel="premium-account"]');
    if (panel) return panel;

    const groupPanel = document.querySelector('[data-panel="premium"]');
    const content = groupPanel?.parentElement || document.querySelector('main.content');
    if (!content) return null;

    panel = document.createElement('section');
    panel.className = 'view hidden';
    panel.dataset.panel = 'premium-account';
    panel.innerHTML = `
      <button class="premium-split-back nav" data-view="shop" type="button">‹ Назад в магазин</button>
      <div class="premium-hero">
        <span class="premium-hero-icon"><svg><use href="#i-diamond"></use></svg></span>
        <div>
          <span class="premium-chip">ПОДПИСКА АККАУНТА</span>
          <h1>Premium аккаунта</h1>
          <p id="premium-account-status">Проверка статуса…</p>
        </div>
      </div>
      <div class="premium-scope-note"><b>Одна подписка на аккаунт.</b> Premium автоматически работает во всех беседах, где этот аккаунт является создателем. Для чужих бесед администратора подписка не наследуется.</div>
      <div id="premium-account-details" class="premium-account-details"></div>
      <div id="premium-account-plans" class="plans" data-premium-plans="account"></div>
      <article class="card">
        <h2>Что входит</h2>
        <div class="feature-grid">
          <span>Все Premium-функции аккаунта</span>
          <span>Premium во всех созданных беседах</span>
          <span>Расширенная автомодерация</span>
          <span>Повышенные лимиты</span>
        </div>
      </article>`;

    if (groupPanel) groupPanel.insertAdjacentElement('beforebegin', panel);
    else content.appendChild(panel);
    return panel;
  }

  function configureGroupPanel() {
    const panel = document.querySelector('[data-panel="premium"]');
    if (!panel) return false;

    panel.dataset.premiumGroupPanel = '1';
    const hero = panel.querySelector('.premium-hero');
    const title = hero?.querySelector('h1');
    if (title) title.textContent = 'Premium группы';
    const chip = hero?.querySelector('.premium-chip');
    if (chip) chip.textContent = 'ПОДПИСКА ГРУППЫ';

    if (!panel.querySelector('[data-premium-group-back]')) {
      const back = document.createElement('button');
      back.className = 'premium-split-back nav';
      back.dataset.view = 'shop';
      back.dataset.premiumGroupBack = '1';
      back.type = 'button';
      back.textContent = '‹ Назад в магазин';
      panel.insertBefore(back, panel.firstChild);
    }

    if (!panel.querySelector('[data-premium-group-note]')) {
      const note = document.createElement('div');
      note.className = 'premium-scope-note';
      note.dataset.premiumGroupNote = '1';
      note.innerHTML = '<b>Отдельная подписка выбранной группы.</b> Она действует только в этой беседе и не переносится в другие группы.';
      hero?.insertAdjacentElement('afterend', note);
    }

    const details = panel.querySelector('#premium-details');
    if (details) details.classList.add('premium-group-details');
    const list = panel.querySelector('#plans-list');
    if (list) list.dataset.premiumPlans = 'group';
    return true;
  }

  function formatDate(value) {
    if (!value) return '—';
    const date = new Date(value);
    if (Number.isNaN(date.getTime())) return String(value);
    return date.toLocaleString('ru-RU');
  }

  function detailCards(items) {
    return items.map(([label, value]) => `
      <div class="premium-detail">
        <small>${escapeHtml(label)}</small>
        <b>${escapeHtml(value)}</b>
      </div>`).join('');
  }

  function accountStatusText() {
    const status = state.accountStatus;
    if (!status?.active) return 'Premium аккаунта не активен';
    if (status.lifetime) return 'Premium аккаунта активен бессрочно';
    return `Активен до ${formatDate(status.until)}`;
  }

  function groupStatusText() {
    if (!currentChatId()) return 'Сначала выберите беседу';
    const status = state.groupStatus;
    if (!status?.active) return 'Premium группы не активен';
    if (status.source === 'owner') return 'Premium действует от аккаунта создателя';
    if (status.lifetime) return 'Premium группы активен бессрочно';
    return `Активен до ${formatDate(status.until)}`;
  }

  function renderPlanList(scope) {
    const list = scope === 'account'
      ? document.querySelector('#premium-account-plans')
      : document.querySelector('#plans-list');
    if (!list) return;

    const plans = state.plans[scope] || [];
    const noChat = scope === 'group' && !currentChatId();
    const buyClass = scope === 'account' ? 'premium-account-buy' : 'premium-group-buy';
    const noun = scope === 'account' ? 'аккаунта' : 'группы';

    list.innerHTML = plans.map(plan => `
      <article class="plan premium-purchase-plan ${Number(plan.months) === 7 ? 'recommended' : ''}" data-premium-store-scope="${scope}">
        <span class="badge">${escapeHtml(plan.badge)}</span>
        <span class="premium-scope-label">${scope === 'account' ? 'АККАУНТ' : 'ГРУППА'}</span>
        <h2>${escapeHtml(plan.title)}</h2>
        <div class="price">${Number(plan.stars).toLocaleString('ru-RU')} ⭐</div>
        <p>${escapeHtml(plan.description)}</p>
        ${Number(plan.discount_percent) > 0 ? `<span class="premium-saving">Скидка ${Number(plan.discount_percent)}%</span>` : ''}
        <button class="primary ${buyClass}" type="button" data-premium-plan="${escapeHtml(plan.code)}" data-premium-plan-scope="${scope}" ${noChat ? 'disabled' : ''}>
          Купить Premium ${noun}
        </button>
      </article>`).join('');
  }

  function renderAccount() {
    if (state.renderingAccount || !ensureAccountPanel()) return;
    state.renderingAccount = true;
    try {
      const status = state.accountStatus;
      const statusNode = document.querySelector('#premium-account-status');
      if (statusNode) statusNode.textContent = accountStatusText();
      const details = document.querySelector('#premium-account-details');
      if (details) {
        details.innerHTML = detailCards([
          ['Тип покупки', 'Premium аккаунта'],
          ['Статус', status?.active ? 'Активен' : 'Неактивен'],
          ['Действует до', status?.lifetime ? 'Бессрочно' : formatDate(status?.until)],
        ]);
      }
      renderPlanList('account');
    } finally {
      state.renderingAccount = false;
    }
  }

  function renderGroup() {
    if (state.renderingGroup || !configureGroupPanel()) return;
    state.renderingGroup = true;
    try {
      const status = state.groupStatus;
      const statusNode = document.querySelector('#premium-status');
      if (statusNode) statusNode.textContent = groupStatusText();
      const details = document.querySelector('#premium-details');
      if (details) {
        const sourceNames = {
          owner: 'Premium аккаунта создателя',
          group: 'Отдельный Premium группы',
          user: 'Premium администратора',
          none: 'Не активен',
        };
        details.innerHTML = detailCards([
          ['Тип покупки', 'Premium группы'],
          ['Статус', status?.active ? 'Активен' : 'Неактивен'],
          ['Источник', sourceNames[status?.source] || 'Не активен'],
        ]);
      }
      renderPlanList('group');
    } finally {
      state.renderingGroup = false;
    }
  }

  async function loadCatalog() {
    try {
      const catalog = await request('/api/premium/purchase-plans');
      if (Array.isArray(catalog?.account) && Array.isArray(catalog?.group)) {
        state.plans = {
          account: catalog.account,
          group: catalog.group,
        };
      }
    } catch (error) {
      console.warn('Premium catalog fallback:', error);
    }
  }

  async function loadAccountStatus() {
    try {
      state.accountStatus = await request('/api/premium/account/status');
    } catch (error) {
      state.accountStatus = null;
      console.warn('Premium account status unavailable:', error);
    }
  }

  async function loadGroupStatus() {
    const chatId = currentChatId();
    if (!chatId) {
      state.groupStatus = null;
      return;
    }
    try {
      state.groupStatus = await request(`/api/chats/${chatId}/premium/status`);
    } catch (error) {
      state.groupStatus = null;
      console.warn('Premium group status unavailable:', error);
    }
  }

  function markShopBottomNavigation() {
    document.querySelectorAll('#bottom-nav .nav').forEach(button => {
      button.classList.toggle('active', button.dataset.view === 'shop');
    });
  }

  function openExternalView(view) {
    document.querySelector('#empty-state')?.classList.add('hidden');
    document.querySelector('#workspace')?.classList.remove('hidden');
    document.querySelectorAll('.view').forEach(panel => {
      panel.classList.toggle('hidden', panel.dataset.panel !== view);
    });
    document.querySelectorAll('.nav[data-view]').forEach(button => {
      button.classList.toggle('active', button.dataset.view === view);
    });
    markShopBottomNavigation();
    try { tg?.BackButton?.show(); } catch (_) {}
    window.scrollTo({ top: 0, behavior: 'smooth' });
  }

  async function buy(scope, planCode, button) {
    const chatId = currentChatId();
    if (scope === 'group' && !chatId) {
      notify('Сначала выберите беседу для покупки Premium группы.', true);
      return;
    }

    button.disabled = true;
    try {
      const url = scope === 'account'
        ? '/api/premium/account/invoice'
        : `/api/chats/${chatId}/premium/invoice`;
      const invoice = await request(url, {
        method: 'POST',
        body: JSON.stringify({ plan_code: planCode }),
      });
      if (!invoice?.invoice_url) throw new Error('Сервер не вернул ссылку на оплату');

      const successText = scope === 'account'
        ? 'Premium аккаунта активирован.'
        : 'Premium выбранной группы активирован.';

      if (tg?.openInvoice) {
        tg.openInvoice(invoice.invoice_url, async paymentStatus => {
          if (paymentStatus === 'paid') {
            notify(successText);
            if (scope === 'account') {
              await loadAccountStatus();
              renderAccount();
            } else {
              await loadGroupStatus();
              renderGroup();
            }
          } else if (paymentStatus === 'failed') {
            notify('Оплата не прошла.', true);
          }
        });
      } else {
        window.open(invoice.invoice_url, '_blank', 'noopener,noreferrer');
        notify('Счёт открыт в новом окне.');
      }
    } catch (error) {
      notify(error.message || String(error), true);
    } finally {
      button.disabled = false;
    }
  }

  document.addEventListener('click', event => {
    const profilePremium = event.target.closest('[data-profile-action="user-premium"]');
    if (profilePremium) {
      event.preventDefault();
      event.stopImmediatePropagation();
      openExternalView('premium-account');
      loadAccountStatus().then(renderAccount);
      return;
    }

    const accountCard = event.target.closest('[data-premium-shop="account"]');
    if (accountCard) {
      setTimeout(() => {
        markShopBottomNavigation();
        loadAccountStatus().then(renderAccount);
      }, 0);
      return;
    }

    const groupCard = event.target.closest('[data-premium-shop="group"]');
    if (groupCard) {
      if (!currentChatId()) {
        event.preventDefault();
        event.stopImmediatePropagation();
        notify('Подключите или выберите беседу для Premium группы.', true);
        return;
      }
      setTimeout(() => {
        markShopBottomNavigation();
        loadGroupStatus().then(renderGroup);
      }, 0);
      return;
    }

    const accountBuy = event.target.closest('.premium-account-buy');
    if (accountBuy) {
      event.preventDefault();
      event.stopImmediatePropagation();
      buy('account', accountBuy.dataset.premiumPlan, accountBuy);
      return;
    }

    const groupBuy = event.target.closest('.premium-group-buy');
    if (groupBuy) {
      event.preventDefault();
      event.stopImmediatePropagation();
      buy('group', groupBuy.dataset.premiumPlan, groupBuy);
    }
  }, true);

  document.addEventListener('change', event => {
    if (event.target?.id !== 'chat-select') return;
    loadGroupStatus().then(renderGroup);
  });

  function installObservers() {
    const groupPlans = document.querySelector('#plans-list');
    if (groupPlans && !groupPlans.dataset.premiumObserverInstalled) {
      groupPlans.dataset.premiumObserverInstalled = '1';
      new MutationObserver(() => {
        if (state.renderingGroup) return;
        const first = groupPlans.firstElementChild;
        if (!first || first.dataset.premiumStoreScope !== 'group') renderGroup();
      }).observe(groupPlans, { childList: true });
    }

    const shopGrid = document.querySelector('.shop-grid');
    if (shopGrid && !shopGrid.dataset.premiumObserverInstalled) {
      shopGrid.dataset.premiumObserverInstalled = '1';
      new MutationObserver(() => splitStoreCards()).observe(shopGrid, { childList: true });
    }
  }

  async function start() {
    installStyles();
    splitStoreCards();
    ensureAccountPanel();
    configureGroupPanel();
    renderAccount();
    renderGroup();
    installObservers();

    await Promise.all([
      loadCatalog(),
      loadAccountStatus(),
      loadGroupStatus(),
    ]);
    renderAccount();
    renderGroup();
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', start, { once: true });
  } else {
    start();
  }
})();
