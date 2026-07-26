(() => {
  'use strict';

  const tg = window.Telegram?.WebApp;
  const state = {
    user: null,
    chats: [],
    chatId: null,
    dashboard: null,
    settings: {},
    settingsDirty: false,
    members: [],
    plans: [],
    currentAction: null,
  };

  const actionDefinitions = [
    {action:'warn', title:'Предупредить', description:'Выдать предупреждение участнику', icon:'i-warning', premium:false, target:true},
    {action:'unwarn', title:'Снять предупреждение', description:'Уменьшить число предупреждений', icon:'i-warning', premium:false, target:true},
    {action:'mute', title:'Мут', description:'Ограничить отправку сообщений', icon:'i-mute', premium:false, target:true, duration:true},
    {action:'unmute', title:'Снять мут', description:'Вернуть возможность писать', icon:'i-unlock', premium:false, target:true},
    {action:'ban', title:'Бан', description:'Заблокировать пользователя', icon:'i-ban', premium:false, target:true, duration:true},
    {action:'unban', title:'Разбан', description:'Снять блокировку пользователя', icon:'i-unlock', premium:false, target:true},
    {action:'purge', title:'Очистка', description:'Удалить последние сообщения', icon:'i-clean', premium:false, amount:true},
    {action:'slow', title:'Медленный режим', description:'Задержка между сообщениями', icon:'i-slow', premium:false, amount:true},
    {action:'lock', title:'Закрыть чат', description:'Писать может только администрация', icon:'i-lock', premium:false},
    {action:'unlock', title:'Открыть чат', description:'Вернуть отправку сообщений', icon:'i-unlock', premium:false},
    {action:'quarantine', title:'Карантин Pro', description:'Ограничить медиа, ссылки и частоту', icon:'i-quarantine', premium:true, target:true, duration:true},
    {action:'susanoo', title:'Экстренная защита', description:'Максимально закрыть беседу', icon:'i-shield', premium:true, duration:true},
    {action:'case', title:'Создать дело', description:'Зафиксировать нарушение', icon:'i-log', premium:true, target:true},
  ];

  const settingGroups = [
    {
      id:'flood', title:'Антифлуд', icon:'i-shield', keywords:'флуд сообщения стикеры лимит',
      fields:[
        {key:'anti_flood_enabled', type:'switch', label:'Включить антифлуд'},
        {key:'flood_limit', type:'range', label:'Лимит сообщений', min:3, max:20},
        {key:'flood_window_seconds', type:'select', label:'Интервал', options:[[10,'10 секунд'],[20,'20 секунд'],[60,'1 минута']]},
        {key:'flood_action', type:'select', label:'Действие', options:[['delete_warn','Удалить и предупредить'],['delete','Только удалить'],['mute','Выдать мут']]},
        {key:'slow_mode_seconds', type:'number', label:'Общая задержка сообщений, секунд', min:0, max:3600},
      ],
    },
    {
      id:'links', title:'Ссылки и реклама', icon:'i-command', keywords:'ссылки реклама домены новичок',
      fields:[
        {key:'link_filter_enabled', type:'switch', label:'Фильтровать ссылки'},
        {key:'links_newbie_hours', type:'select', label:'Ограничивать новичков', options:[[1,'1 час'],[6,'6 часов'],[24,'24 часа'],[168,'7 дней']]},
        {key:'allowed_domains', type:'textarea-list', label:'Разрешённые домены'},
        {key:'mass_mentions_limit', type:'number', label:'Максимум упоминаний', min:1, max:50},
      ],
    },
    {
      id:'words', title:'Запрещённые слова', icon:'i-report', keywords:'слова мат фильтр запрещенные',
      fields:[
        {key:'word_filter_enabled', type:'switch', label:'Включить фильтр'},
        {key:'blocked_words', type:'textarea-list', label:'Слова, по одному на строку'},
        {key:'symbol_replacement_check', type:'switch', label:'Учитывать замену букв символами'},
      ],
    },
    {
      id:'captcha', title:'CAPTCHA и новички', icon:'i-user', keywords:'captcha новичок проверка',
      fields:[
        {key:'captcha_enabled', type:'switch', label:'Проверять новых участников'},
        {key:'captcha_timeout_seconds', type:'select', label:'Время на проверку', options:[[60,'1 минута'],[180,'3 минуты'],[300,'5 минут']]},
      ],
    },
    {
      id:'punishments', title:'Система наказаний', icon:'i-warning', keywords:'наказания мут бан предупреждения',
      fields:[
        {key:'warn_threshold', type:'select', label:'Мут после предупреждений', options:[[0,'Только вручную'],[2,'2 предупреждения'],[3,'3 предупреждения'],[5,'5 предупреждений']]},
        {key:'default_mute_seconds', type:'select', label:'Стандартный мут', options:[[1800,'30 минут'],[3600,'1 час'],[21600,'6 часов'],[86400,'24 часа']]},
        {key:'warnings_expire_days', type:'number', label:'Сгорание предупреждений, дней', min:0, max:365},
      ],
    },
    {
      id:'reports', title:'Жалобы', icon:'i-report', keywords:'жалобы report обращения',
      fields:[
        {key:'reports_enabled', type:'switch', label:'Включить /report'},
        {key:'report_hide_threshold', type:'number', label:'Скрывать сообщение после жалоб', min:1, max:20},
      ],
    },
    {
      id:'rp', title:'RP-команды', icon:'i-rp', keywords:'rp команды ответы задержка',
      fields:[
        {key:'rp_enabled', type:'switch', label:'Разрешить RP-команды'},
        {key:'rp_default_cooldown', type:'number', label:'Задержка по умолчанию, секунд', min:0, max:86400},
      ],
    },
    {
      id:'anime', title:'Аниме и игровые функции', icon:'i-rp', keywords:'аниме стиль ранги экономика игра',
      fields:[
        {key:'anime_enabled', type:'switch', label:'Полный аниме-режим'},
        {key:'anime_replies', type:'switch', label:'Аниме-стиль ответов', depends:'anime_enabled'},
        {key:'anime_style', type:'select', label:'Стиль ответов', depends:'anime_enabled', options:[['shinobi','Shinobi'],['classic','Anime Classic'],['cyber','Cyber Anime'],['neutral','Нейтральный']]},
        {key:'ranks_enabled', type:'switch', label:'Ранги и опыт', depends:'anime_enabled'},
        {key:'economy_enabled', type:'switch', label:'AniCoin', depends:'anime_enabled'},
        {key:'xp_per_message', type:'number', label:'XP за сообщение', min:0, max:100, depends:'ranks_enabled'},
        {key:'coins_per_message', type:'number', label:'AniCoin за сообщение', min:0, max:100, depends:'economy_enabled'},
      ],
    },
    {
      id:'premium', title:'Premium-модули', icon:'i-premium', keywords:'premium карантин дела статистика расписание', premium:true,
      fields:[
        {key:'premium_quarantine', type:'switch', label:'Карантин Pro'},
        {key:'premium_cases', type:'switch', label:'Дела и доказательства'},
        {key:'premium_schedule', type:'switch', label:'Расписание защиты'},
        {key:'premium_stats', type:'switch', label:'Расширенная статистика'},
      ],
    },
  ];

  const $ = (selector, root=document) => root.querySelector(selector);
  const $$ = (selector, root=document) => [...root.querySelectorAll(selector)];
  const icon = id => `<span class="icon-box"><svg><use href="#${id}"></use></svg></span>`;
  const iconSmall = id => `<span class="icon-box small"><svg><use href="#${id}"></use></svg></span>`;

  async function api(path, options={}) {
    const headers = new Headers(options.headers || {});
    headers.set('Content-Type', 'application/json');
    if (tg?.initData) headers.set('X-Telegram-Init-Data', tg.initData);
    else {
      const devUserId = new URLSearchParams(window.location.search).get('dev_user_id');
      if (devUserId) headers.set('X-Dev-User-Id', devUserId);
    }
    const response = await fetch(path, {...options, headers});
    if (!response.ok) {
      let detail = `Ошибка ${response.status}`;
      try { detail = (await response.json()).detail || detail; } catch (_) {}
      throw new Error(detail);
    }
    if (response.status === 204) return null;
    return response.json();
  }

  function notify(message, error=false) {
    const node = $('#notice');
    node.textContent = message;
    node.classList.remove('hidden');
    node.style.borderColor = error ? '#b94d5e' : '';
    clearTimeout(notify.timer);
    notify.timer = setTimeout(() => node.classList.add('hidden'), 3500);
    if (tg?.HapticFeedback) tg.HapticFeedback.notificationOccurred(error ? 'error' : 'success');
  }

  function setView(view) {
    $$('.view').forEach(panel => panel.classList.toggle('hidden', panel.dataset.panel !== view));
    $$('.nav').forEach(button => button.classList.toggle('active', button.dataset.view === view));
    window.scrollTo({top:0, behavior:'smooth'});
  }

  function currentChat() {
    return state.chats.find(chat => chat.id === state.chatId);
  }

  async function init() {
    try {
      tg?.ready();
      tg?.expand();
      state.user = await api('/api/me');
      $('#user-line').textContent = state.user.username ? `@${state.user.username}` : state.user.first_name;
      state.chats = await api('/api/chats');
      state.plans = await api('/api/premium/plans');
      renderChatSelect();
      renderActions();
      renderSettings();
      renderPlans();
      bindEvents();
      if (!state.chats.length) {
        $('#empty-state').classList.remove('hidden');
        return;
      }
      $('#workspace').classList.remove('hidden');
      $('#bottom-nav').classList.remove('hidden');
      state.chatId = state.chats[0].id;
      $('#chat-select').value = String(state.chatId);
      await loadChat();
    } catch (error) {
      $('#empty-state').classList.remove('hidden');
      $('#empty-state h1').textContent = 'Не удалось открыть Mini App';
      $('#empty-state p').textContent = error.message;
      notify(error.message, true);
    }
  }

  function renderChatSelect() {
    const select = $('#chat-select');
    select.innerHTML = state.chats.length
      ? state.chats.map(chat => `<option value="${chat.id}">${escapeHtml(chat.title)}${chat.premium ? ' · Premium' : ''}</option>`).join('')
      : '<option>Нет бесед</option>';
  }

  async function loadChat() {
    if (!state.chatId) return;
    try {
      const [dashboard, members, logs, reports, rp, rules, top] = await Promise.all([
        api(`/api/chats/${state.chatId}/dashboard`),
        api(`/api/chats/${state.chatId}/members?limit=100`),
        api(`/api/chats/${state.chatId}/logs?limit=50`),
        api(`/api/chats/${state.chatId}/reports`),
        api(`/api/chats/${state.chatId}/rp`),
        api(`/api/chats/${state.chatId}/rules`),
        api('/api/top-chats'),
      ]);
      state.dashboard = dashboard;
      const selectedChat = currentChat();
      if (selectedChat) {
        selectedChat.premium = dashboard.chat.premium;
        selectedChat.premium_until = dashboard.chat.premium_until;
      }
      state.settings = structuredClone(dashboard.settings);
      state.settingsDirty = false;
      state.members = members;
      renderMetrics();
      renderMembersInDialog();
      renderLogs(logs);
      renderReports(reports);
      renderRp(rp);
      renderRules(rules);
      renderTop(top);
      renderSettingsValues();
      updatePremiumStatus();
    } catch (error) {
      notify(error.message, true);
    }
  }

  function renderMetrics() {
    const metrics = state.dashboard.metrics;
    const chat = state.dashboard.chat;
    const items = [
      ['i-user','Участники',metrics.members],
      ['i-log','Действия сегодня',metrics.actions_today],
      ['i-report','Открытые жалобы',metrics.open_reports],
      ['i-rp','RP-команды',metrics.rp_commands],
      ['i-shield','Правила',metrics.rules],
    ];
    $('#metrics').innerHTML = items.map(([ic,label,value]) => `<div class="metric">${iconSmall(ic)}<span>${label}</span><strong>${value}</strong></div>`).join('');
    $('#premium-status').textContent = chat.premium
      ? `Premium активен до ${new Date(chat.premium_until).toLocaleDateString('ru-RU')}`
      : 'Premium не активен';
  }

  function renderActions() {
    const create = action => `<button type="button" class="action ${action.premium ? 'premium' : ''}" data-action="${action.action}" data-search="${(action.title+' '+action.description).toLowerCase()}">${iconSmall(action.icon)}<b>${action.title}</b><small>${action.description}</small></button>`;
    const regular = actionDefinitions.filter(a => !a.premium);
    const premium = actionDefinitions.filter(a => a.premium);
    $('#quick-actions').innerHTML = [...regular.slice(0,5), premium[1]].map(create).join('');
    $('#regular-actions').innerHTML = regular.map(create).join('');
    $('#premium-actions').innerHTML = premium.map(create).join('');
  }

  function renderMembersInDialog() {
    const select = $('#action-target');
    select.innerHTML = state.members.length
      ? state.members.map(user => `<option value="${user.id}">${escapeHtml(user.first_name)}${user.username ? ` (@${escapeHtml(user.username)})` : ''}</option>`).join('')
      : '<option value="">Нет известных участников</option>';
  }

  function renderLogs(logs) {
    const html = logs.length ? logs.map(row => `<div class="list-row"><b>${escapeHtml(row.action)}</b><p>${escapeHtml(row.reason || 'Без причины')} · ${new Date(row.created_at).toLocaleString('ru-RU')}</p></div>`).join('') : '<p class="muted">Журнал пока пуст.</p>';
    $('#overview-logs').innerHTML = html;
  }

  function renderReports(reports) {
    $('#reports-list').innerHTML = reports.length ? reports.map(report => `
      <article class="list-card" data-report-id="${report.id}">
        <div>${icon('i-report')}<h3>Жалоба AG-${report.id}</h3><p>Пользователь ${report.target_id} · ${escapeHtml(report.reason)} · ${new Date(report.created_at).toLocaleString('ru-RU')}</p></div>
        <div class="actions">
          <button class="secondary report-decision" data-decision="dismiss">Отклонить</button>
          <button class="secondary report-decision" data-decision="warn">Предупредить</button>
          <button class="secondary report-decision" data-decision="mute">Мут</button>
          <button class="danger report-decision" data-decision="ban">Бан</button>
        </div>
      </article>`).join('') : '<article class="card muted">Открытых жалоб нет.</article>';
  }

  function renderRp(commands) {
    $('#rp-list').innerHTML = commands.length ? commands.map(command => `
      <article class="list-card" data-rp-id="${command.id}">
        <div>${icon(command.is_premium ? 'i-premium' : 'i-rp')}<h3>${escapeHtml(command.name)}</h3><p>${escapeHtml(command.response_template)} · задержка ${command.cooldown_seconds} сек.</p></div>
        <div class="actions">
          <button class="secondary rp-toggle" data-enabled="${command.enabled}">${command.enabled ? 'Отключить' : 'Включить'}</button>
          <button class="danger rp-delete">Удалить</button>
        </div>
      </article>`).join('') : '<article class="card muted">RP-команд пока нет.</article>';
  }

  function renderRules(rules) {
    $('#rules-list').innerHTML = rules.length ? rules.map(rule => `
      <article class="list-card" data-rule-id="${rule.id}">
        <div>${icon(rule.is_premium ? 'i-premium' : 'i-shield')}<h3>${escapeHtml(rule.name)}</h3><p>${escapeHtml(JSON.stringify(rule.condition))} → ${escapeHtml(JSON.stringify(rule.actions))}</p></div>
        <div class="actions"><button class="secondary rule-toggle" data-enabled="${rule.enabled}">${rule.enabled ? 'Отключить' : 'Включить'}</button><button class="danger rule-delete">Удалить</button></div>
      </article>`).join('') : '<article class="card muted">Правила ещё не созданы.</article>';
  }

  function renderTop(rows) {
    $('#top-list').innerHTML = rows.map(row => `<article class="list-card"><div>${icon('i-top')}<h3>${row.place}. ${escapeHtml(row.title)}</h3><p>${row.members} участников${row.premium ? ' · Premium' : ''}</p></div></article>`).join('');
  }

  function renderSettings() {
    $('#settings-list').innerHTML = settingGroups.map(group => `
      <details class="setting-group" data-search="${escapeHtml(group.keywords)}">
        <summary><div class="section-title">${icon(group.icon)}<div><h2>${escapeHtml(group.title)}</h2><p id="summary-${group.id}">Открыть настройки</p></div></div></summary>
        <div class="accordion-body" id="group-${group.id}">${group.fields.map(fieldHtml).join('')}</div>
      </details>`).join('');
  }

  function fieldHtml(field) {
    const dependent = field.depends ? ` data-depends="${field.depends}"` : '';
    if (field.type === 'switch') return `<label class="switch-line"${dependent}><input id="setting-${field.key}" data-key="${field.key}" type="checkbox"><span>${escapeHtml(field.label)}</span></label>`;
    if (field.type === 'select') return `<label${dependent}>${escapeHtml(field.label)}<select id="setting-${field.key}" data-key="${field.key}" class="control">${field.options.map(([value,label]) => `<option value="${value}">${escapeHtml(label)}</option>`).join('')}</select></label>`;
    if (field.type === 'range') return `<label${dependent}>${escapeHtml(field.label)}: <b id="range-${field.key}"></b><input id="setting-${field.key}" data-key="${field.key}" type="range" min="${field.min}" max="${field.max}"></label>`;
    if (field.type === 'number') return `<label${dependent}>${escapeHtml(field.label)}<input id="setting-${field.key}" data-key="${field.key}" class="control" type="number" min="${field.min}" max="${field.max}"></label>`;
    if (field.type === 'textarea-list') return `<label${dependent}>${escapeHtml(field.label)}<textarea id="setting-${field.key}" data-key="${field.key}" class="control" rows="4"></textarea></label>`;
    return '';
  }

  function renderSettingsValues() {
    settingGroups.forEach(group => {
      group.fields.forEach(field => {
        const control = $(`#setting-${field.key}`);
        if (!control) return;
        const value = state.settings[field.key];
        if (field.type === 'switch') control.checked = Boolean(value);
        else if (field.type === 'textarea-list') control.value = Array.isArray(value) ? value.join('\n') : '';
        else control.value = value ?? '';
        if (field.type === 'range') $(`#range-${field.key}`).textContent = control.value;
      });
    });
    refreshSettingDependencies();
    updateSettingSummaries();
  }

  function readSettingsFromUi() {
    settingGroups.forEach(group => group.fields.forEach(field => {
      const control = $(`#setting-${field.key}`);
      if (!control) return;
      let value;
      if (field.type === 'switch') value = control.checked;
      else if (field.type === 'textarea-list') value = control.value.split('\n').map(v => v.trim()).filter(Boolean);
      else if (field.type === 'number' || field.type === 'range') value = Number(control.value);
      else {
        const raw = control.value;
        const option = field.options?.find(([val]) => String(val) === raw);
        value = option && typeof option[0] === 'number' ? Number(raw) : raw;
      }
      state.settings[field.key] = value;
    }));
  }

  function refreshSettingDependencies() {
    $$('[data-depends]').forEach(label => {
      const key = label.dataset.depends;
      const enabled = Boolean(state.settings[key]);
      label.style.opacity = enabled ? '1' : '.45';
      $('input,select,textarea', label).disabled = !enabled;
    });
    const premium = currentChat()?.premium;
    const premiumGroup = $('.setting-group[data-search*="premium"]');
    if (premiumGroup) {
      premiumGroup.style.opacity = premium ? '1' : '.55';
      $$('input,select,textarea', premiumGroup).forEach(control => control.disabled = !premium);
    }
  }

  function updateSettingSummaries() {
    $('#summary-flood').textContent = state.settings.anti_flood_enabled ? `${state.settings.flood_limit} сообщений за ${state.settings.flood_window_seconds} сек.` : 'Отключён';
    $('#summary-links').textContent = state.settings.link_filter_enabled ? `Новички: ${state.settings.links_newbie_hours} ч.` : 'Отключены';
    $('#summary-words').textContent = state.settings.word_filter_enabled ? `${state.settings.blocked_words?.length || 0} слов` : 'Отключены';
    $('#summary-captcha').textContent = state.settings.captcha_enabled ? `${state.settings.captcha_timeout_seconds} сек.` : 'Отключена';
    $('#summary-punishments').textContent = state.settings.warn_threshold ? `${state.settings.warn_threshold} предупреждения → мут` : 'Только вручную';
    $('#summary-reports').textContent = state.settings.reports_enabled ? 'Включены' : 'Отключены';
    $('#summary-rp').textContent = state.settings.rp_enabled ? `Задержка ${state.settings.rp_default_cooldown} сек.` : 'Отключены';
    $('#summary-anime').textContent = state.settings.anime_enabled ? state.settings.anime_style : 'Полностью отключено';
    $('#summary-premium').textContent = currentChat()?.premium ? 'Доступно' : 'Требуется Premium';
  }

  function renderPlans() {
    $('#plans-list').innerHTML = state.plans.map(plan => `
      <article class="plan ${plan.code === 'season' ? 'recommended' : ''}">
        <span class="badge">${escapeHtml(plan.badge)}</span>
        <h2>${escapeHtml(plan.title)}</h2>
        <div class="price">${plan.stars} ⭐</div>
        <p>${escapeHtml(plan.description)}</p>
        <button class="primary buy-plan" data-plan="${plan.code}" type="button">Купить на ${plan.days} дней</button>
      </article>`).join('');
  }

  function updatePremiumStatus() {
    const chat = currentChat();
    $('#premium-status').textContent = chat?.premium
      ? `Активен до ${new Date(chat.premium_until).toLocaleDateString('ru-RU')}`
      : 'Выберите период и оплатите Telegram Stars';
  }

  function openAction(actionName) {
    const action = actionDefinitions.find(item => item.action === actionName);
    if (!action) return;
    if (action.premium && !currentChat()?.premium) {
      setView('premium');
      notify('Для этой команды нужен Premium.', true);
      return;
    }
    state.currentAction = action;
    $('#action-title').textContent = action.title;
    $('#action-description').textContent = action.description;
    $('#action-icon use').setAttribute('href', `#${action.icon}`);
    $('#action-target-row').classList.toggle('hidden', !action.target);
    $('#action-duration-row').classList.toggle('hidden', !action.duration);
    $('#action-amount-row').classList.toggle('hidden', !action.amount);
    $('#action-amount').value = action.action === 'slow' ? 15 : 25;
    $('#action-reason').value = '';
    $('#action-dialog').showModal();
  }

  async function executeAction() {
    const action = state.currentAction;
    if (!action) return;
    const body = {
      action: action.action,
      reason: $('#action-reason').value,
    };
    if (action.target) body.target_id = Number($('#action-target').value);
    if (action.duration) body.duration_seconds = Number($('#action-duration').value);
    if (action.amount) body.amount = Number($('#action-amount').value);
    try {
      await api(`/api/chats/${state.chatId}/actions`, {method:'POST', body:JSON.stringify(body)});
      $('#action-dialog').close();
      notify(`Команда «${action.title}» выполнена.`);
      await loadChat();
    } catch (error) { notify(error.message, true); }
  }

  async function createRule() {
    const payload = {
      name: $('#rule-name').value.trim(),
      condition: {type: $('#rule-condition').value},
      actions: [{type: $('#rule-action').value}],
      enabled: true,
      is_premium: $('#rule-premium').checked,
    };
    try {
      await api(`/api/chats/${state.chatId}/rules`, {method:'POST', body:JSON.stringify(payload)});
      notify('Правило создано.');
      await loadChat();
    } catch (error) { notify(error.message, true); }
  }

  async function createRp() {
    const payload = {
      name: $('#rp-name').value.trim(),
      aliases: $('#rp-aliases').value.split(',').map(v => v.trim()).filter(Boolean),
      response_template: $('#rp-template').value.trim(),
      response_variants: $('#rp-variants').value.split('\n').map(v => v.trim()).filter(Boolean),
      enabled: true,
      is_premium: $('#rp-premium').checked,
      cooldown_seconds: Number($('#rp-cooldown').value),
      access: $('#rp-access').value,
      reward_xp: Number($('#rp-xp').value),
      reward_coins: Number($('#rp-coins').value),
    };
    try {
      await api(`/api/chats/${state.chatId}/rp`, {method:'POST', body:JSON.stringify(payload)});
      notify('RP-команда добавлена.');
      await loadChat();
    } catch (error) { notify(error.message, true); }
  }

  async function saveSettings() {
    readSettingsFromUi();
    try {
      const result = await api(`/api/chats/${state.chatId}/settings`, {method:'PUT', body:JSON.stringify({settings:state.settings})});
      state.settings = result.settings;
      state.settingsDirty = false;
      notify('Настройки сохранены.');
      renderSettingsValues();
      await loadChat();
    } catch (error) { notify(error.message, true); }
  }

  async function buyPlan(planCode) {
    try {
      const result = await api(`/api/chats/${state.chatId}/premium/invoice`, {method:'POST', body:JSON.stringify({plan_code:planCode})});
      if (!tg?.openInvoice) throw new Error('Оплата доступна только внутри Telegram');
      tg.openInvoice(result.invoice_url, async status => {
        if (status === 'paid') {
          notify('Оплата прошла. Premium активируется автоматически.');
          setTimeout(loadChat, 1200);
        } else if (status === 'failed') notify('Оплата не прошла.', true);
      });
    } catch (error) { notify(error.message, true); }
  }

  function bindEvents() {
    $$('.nav').forEach(button => button.addEventListener('click', () => setView(button.dataset.view)));
    $('#chat-select').addEventListener('change', async event => {
      state.chatId = Number(event.target.value);
      await loadChat();
      notify('Беседа переключена.');
    });
    document.addEventListener('click', async event => {
      const actionButton = event.target.closest('[data-action]');
      if (actionButton) openAction(actionButton.dataset.action);

      const reportButton = event.target.closest('.report-decision');
      if (reportButton) {
        const card = reportButton.closest('[data-report-id]');
        try {
          await api(`/api/chats/${state.chatId}/reports/${card.dataset.reportId}/decision`, {method:'POST', body:JSON.stringify({decision:reportButton.dataset.decision, duration_seconds:3600, reason:'Решение из Mini App'})});
          notify('Решение по жалобе выполнено.');
          await loadChat();
        } catch (error) { notify(error.message, true); }
      }

      const rpDelete = event.target.closest('.rp-delete');
      if (rpDelete) {
        const id = rpDelete.closest('[data-rp-id]').dataset.rpId;
        try { await api(`/api/chats/${state.chatId}/rp/${id}`, {method:'DELETE'}); notify('RP-команда удалена.'); await loadChat(); } catch(error){ notify(error.message,true); }
      }
      const rpToggle = event.target.closest('.rp-toggle');
      if (rpToggle) {
        const card = rpToggle.closest('[data-rp-id]');
        const enabled = rpToggle.dataset.enabled !== 'true';
        try { await api(`/api/chats/${state.chatId}/rp/${card.dataset.rpId}`, {method:'PATCH', body:JSON.stringify({enabled})}); notify('Состояние команды изменено.'); await loadChat(); } catch(error){ notify(error.message,true); }
      }
      const ruleDelete = event.target.closest('.rule-delete');
      if (ruleDelete) {
        const id = ruleDelete.closest('[data-rule-id]').dataset.ruleId;
        try { await api(`/api/chats/${state.chatId}/rules/${id}`, {method:'DELETE'}); notify('Правило удалено.'); await loadChat(); } catch(error){ notify(error.message,true); }
      }
      const ruleToggle = event.target.closest('.rule-toggle');
      if (ruleToggle) {
        const card = ruleToggle.closest('[data-rule-id]');
        const enabled = ruleToggle.dataset.enabled !== 'true';
        try { await api(`/api/chats/${state.chatId}/rules/${card.dataset.ruleId}`, {method:'PATCH', body:JSON.stringify({enabled})}); notify('Состояние правила изменено.'); await loadChat(); } catch(error){ notify(error.message,true); }
      }
      const buyButton = event.target.closest('.buy-plan');
      if (buyButton) buyPlan(buyButton.dataset.plan);
    });

    $('#close-action').addEventListener('click', () => $('#action-dialog').close());
    $('#execute-action').addEventListener('click', executeAction);
    $('#create-rule').addEventListener('click', createRule);
    $('#create-rp').addEventListener('click', createRp);
    $('#save-settings').addEventListener('click', saveSettings);

    $('#command-search').addEventListener('input', event => {
      const query = event.target.value.toLowerCase().trim();
      $$('#regular-actions .action, #premium-actions .action').forEach(button => button.classList.toggle('hidden', !button.dataset.search.includes(query)));
    });
    $('#settings-search').addEventListener('input', event => {
      const query = event.target.value.toLowerCase().trim();
      $$('.setting-group').forEach(group => {
        const match = group.dataset.search.includes(query) || group.textContent.toLowerCase().includes(query);
        group.classList.toggle('hidden', !match);
        if (query && match) group.open = true;
      });
    });
    $('#settings-list').addEventListener('input', event => {
      const key = event.target.dataset.key;
      if (!key) return;
      readSettingsFromUi();
      state.settingsDirty = true;
      if (event.target.type === 'range') $(`#range-${key}`).textContent = event.target.value;
      refreshSettingDependencies();
      updateSettingSummaries();
    });
    $('#settings-list').addEventListener('change', event => {
      if (!event.target.dataset.key) return;
      readSettingsFromUi();
      refreshSettingDependencies();
      updateSettingSummaries();
    });
  }

  function escapeHtml(value) {
    return String(value ?? '').replace(/[&<>'"]/g, char => ({'&':'&amp;','<':'&lt;','>':'&gt;',"'":'&#39;','"':'&quot;'}[char]));
  }

  init();
})();
