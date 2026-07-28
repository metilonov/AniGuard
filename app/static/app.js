(() => {
  'use strict';

  const tg = window.Telegram?.WebApp;
  const state = {
    user: null,
    chats: [],
    chatId: null,
    dashboard: null,
    members: [],
    settings: {},
    plans: [],
    currentAction: null,
    customCommands: [],
    gameCommands: [],
    editingCustomId: null,
    editingGameId: null,
    profile: null,
    logs: [],
    currentView: 'overview',
    basicCommands: [],
    rulesConfig: {rules:[]},
    welcome: null,
    captcha: null,
    premiumStatus: null,
    editingBasicKey: null,
    captchaPreviewAnswer: null,
  };

  const $ = (selector, root = document) => root.querySelector(selector);
  const $$ = (selector, root = document) => [...root.querySelectorAll(selector)];
  const escapeHtml = value => String(value ?? '').replace(/[&<>'"]/g, char => ({'&':'&amp;','<':'&lt;','>':'&gt;',"'":'&#39;','"':'&quot;'}[char]));
  const icon = id => `<span class="icon-box"><svg><use href="#${id}"></use></svg></span>`;
  const iconSmall = id => `<span class="icon-box small"><svg><use href="#${id}"></use></svg></span>`;

  const actionDefinitions = [
    {action:'warn', title:'Предупреждение', description:'Выдать предупреждение', icon:'i-warning', target:true},
    {action:'unwarn', title:'Снять предупреждение', description:'Уменьшить число предупреждений', icon:'i-warning-off', target:true},
    {action:'mute', title:'Мут', description:'Запретить отправку сообщений', icon:'i-mute', target:true, duration:true},
    {action:'unmute', title:'Снять мут', description:'Вернуть возможность писать', icon:'i-volume', target:true},
    {action:'ban', title:'Бан', description:'Заблокировать пользователя', icon:'i-ban', target:true, duration:true},
    {action:'unban', title:'Разбан', description:'Снять блокировку', icon:'i-unban', target:true},
    {action:'kick', title:'Исключить', description:'Удалить с возможностью вернуться', icon:'i-kick', target:true},
    {action:'restrict_media', title:'Запрет медиа', description:'Запретить фото, видео и файлы', icon:'i-image-lock', target:true, duration:true},
    {action:'unrestrict_media', title:'Разрешить медиа', description:'Снять ограничение медиа', icon:'i-image-check', target:true},
    {action:'restrict_links', title:'Запрет ссылок', description:'Удалять ссылки пользователя', icon:'i-link-lock', target:true, duration:true},
    {action:'unrestrict_links', title:'Разрешить ссылки', description:'Снять запрет ссылок', icon:'i-link-check', target:true},
    {action:'restrict_commands', title:'Блокировка команд', description:'Запретить игровые и кастомные команды', icon:'i-terminal-lock', target:true, duration:true},
    {action:'unrestrict_commands', title:'Разрешить команды', description:'Снять блокировку команд', icon:'i-terminal-check', target:true},
    {action:'quarantine', title:'Карантин Pro', description:'Оставить только безопасные действия', icon:'i-quarantine', target:true, duration:true, premium:true},
    {action:'unquarantine', title:'Снять карантин', description:'Вернуть обычные разрешения', icon:'i-quarantine-off', target:true},
    {action:'purge', title:'Очистка', description:'Удалить последние сообщения', icon:'i-broom', amount:true},
    {action:'slow', title:'Медленный режим', description:'Установить задержку между сообщениями', icon:'i-timer', amount:true},
    {action:'lock', title:'Закрыть чат', description:'Запретить сообщения участникам', icon:'i-lock'},
    {action:'unlock', title:'Открыть чат', description:'Вернуть отправку сообщений', icon:'i-unlock'},
    {action:'susanoo', title:'Экстренная защита', description:'Мгновенно закрыть чат при атаке', icon:'i-susanoo', premium:true},
  ];

  const settingGroups = [
    {id:'frequency',title:'Частота сообщений',icon:'i-filter',keywords:'флуд повторы строки длина эмодзи хэштеги упоминания команды',automod:true,fields:[
      {key:'anti_flood_enabled',type:'switch',label:'Антифлуд',description:'Ограничивает слишком частые сообщения',icon:'i-filter'},
      {key:'duplicate_filter_enabled',type:'switch',label:'Повторные сообщения',description:'Удаляет одинаковый текст и копипаст',icon:'i-copy'},
      {key:'line_flood_enabled',type:'switch',label:'Флуд строками',description:'Ограничивает большое количество строк',icon:'i-document'},
      {key:'long_message_filter_enabled',type:'switch',label:'Длинные сообщения',description:'Удаляет текст длиннее заданного лимита',icon:'i-message-star'},
      {key:'emoji_flood_enabled',type:'switch',label:'Эмодзи-флуд',description:'Контролирует количество обычных эмодзи',icon:'i-emoji'},
      {key:'hashtag_flood_enabled',type:'switch',label:'Хэштег-флуд',description:'Ограничивает массовые хэштеги',icon:'i-at'},
      {key:'mass_mentions_enabled',type:'switch',label:'Массовые упоминания',description:'Блокирует спам через @username',icon:'i-users'},
      {key:'command_flood_enabled',type:'switch',label:'Флуд командами',description:'Контролирует частые вызовы команд бота',icon:'i-command-panel'},
    ]},
    {id:'text-content',title:'Текст и содержимое',icon:'i-document',keywords:'капс слова символы правки обход спам алфавит финансы розыгрыш',automod:true,fields:[
      {key:'caps_filter_enabled',type:'switch',label:'Капс-фильтр',description:'Контролирует сообщения заглавными буквами',icon:'i-caps'},
      {key:'word_filter_enabled',type:'switch',label:'Запрещённые слова',description:'Использует словарь выбранной группы',icon:'i-warning'},
      {key:'invisible_symbols_filter_enabled',type:'switch',label:'Невидимые символы',description:'Обнаруживает скрытые управляющие символы',icon:'i-hidden-link'},
      {key:'edited_message_filter_enabled',type:'switch',label:'Проверка редактирования',description:'Повторно анализирует изменённые сообщения',icon:'i-edit',premium:true},
      {key:'obfuscation_filter_enabled',type:'switch',label:'Обход запрещённых слов',description:'Распознаёт замену букв и символов',icon:'i-spark',premium:true},
      {key:'smart_spam_enabled',type:'switch',label:'Умный антиспам',description:'Определяет замаскированную рекламу',icon:'i-brain',premium:true},
      {key:'mixed_alphabet_filter_enabled',type:'switch',label:'Смешение алфавитов',description:'Ищет подмену кириллицы латиницей',icon:'i-id',premium:true},
      {key:'financial_spam_filter_enabled',type:'switch',label:'Финансовый спам',description:'Фильтрует подозрительные предложения',icon:'i-diamond',premium:true},
      {key:'fake_giveaway_filter_enabled',type:'switch',label:'Поддельные розыгрыши',description:'Определяет мошеннические конкурсы',icon:'i-crown',premium:true},
    ]},
    {id:'links',title:'Ссылки',icon:'i-link-lock',keywords:'ссылки приглашения сокращенные скрытые фишинг домены',automod:true,fields:[
      {key:'link_filter_enabled',type:'switch',label:'Фильтр ссылок новичков',description:'Удаляет неизвестные домены у новых участников',icon:'i-link-lock'},
      {key:'invite_link_filter_enabled',type:'switch',label:'Приглашения в другие чаты',description:'Блокирует сторонние Telegram-приглашения',icon:'i-link'},
      {key:'short_link_filter_enabled',type:'switch',label:'Сокращённые ссылки',description:'Проверяет скрытые переадресации',icon:'i-hidden-link',premium:true},
      {key:'hidden_link_filter_enabled',type:'switch',label:'Скрытые ссылки',description:'Проверяет URL внутри текста',icon:'i-link-check',premium:true},
      {key:'phishing_filter_enabled',type:'switch',label:'Антифишинг',description:'Определяет опасные и поддельные сайты',icon:'i-suspicious',premium:true},
      {key:'domain_whitelist_enabled',type:'switch',label:'Белый список доменов',description:'Разрешает только доверенные сайты',icon:'i-document',premium:true},
    ]},
    {id:'media',title:'Медиа и типы сообщений',icon:'i-media',keywords:'медиа стикеры голосовые файлы контакты фото опрос игры эмодзи',automod:true,fields:[
      {key:'media_filter_enabled',type:'switch',label:'Ограничение медиа',description:'Контролирует фото, видео, файлы и GIF',icon:'i-media'},
      {key:'sticker_flood_enabled',type:'switch',label:'Стикер-флуд',description:'Ограничивает частые стикеры',icon:'i-message-star'},
      {key:'voice_flood_enabled',type:'switch',label:'Голосовые и видеокружки',description:'Контролирует частые голосовые сообщения',icon:'i-volume'},
      {key:'dangerous_file_filter_enabled',type:'switch',label:'Опасные файлы',description:'Блокирует запрещённые расширения',icon:'i-document'},
      {key:'contact_location_filter_enabled',type:'switch',label:'Контакты и геолокация',description:'Запрещает отправку личных данных',icon:'i-id'},
      {key:'image_text_filter_enabled',type:'switch',label:'Текст медиа',description:'Проверяет подписи и имена файлов',icon:'i-image-lock',premium:true},
      {key:'media_duplicate_filter_enabled',type:'switch',label:'Повтор одинакового медиа',description:'Находит повторную отправку одного файла',icon:'i-copy',premium:true},
      {key:'poll_filter_enabled',type:'switch',label:'Опросы',description:'Ограничивает создание опросов',icon:'i-activity'},
      {key:'game_filter_enabled',type:'switch',label:'Встроенные игры',description:'Контролирует игровые сообщения Telegram',icon:'i-gamepad'},
      {key:'custom_emoji_flood_enabled',type:'switch',label:'Кастомные эмодзи',description:'Ограничивает Premium-эмодзи в сообщении',icon:'i-emoji',premium:true},
    ]},
    {id:'sources',title:'Источники сообщений',icon:'i-forward',keywords:'пересылки каналы координированный спам',automod:true,fields:[
      {key:'forward_filter_enabled',type:'switch',label:'Пересланные сообщения',description:'Контролирует пересылки из других чатов',icon:'i-forward'},
      {key:'channel_sender_filter_enabled',type:'switch',label:'Сообщения от каналов',description:'Блокирует отправку от имени сторонних каналов',icon:'i-chat'},
      {key:'coordinated_spam_enabled',type:'switch',label:'Координированный спам',description:'Находит одинаковые действия нескольких аккаунтов',icon:'i-users',premium:true},
    ]},
    {id:'newcomers',title:'Новые участники',icon:'i-captcha',keywords:'капча рейд аккаунт профиль карантин медиа новичков',automod:true,fields:[
      {key:'captcha_enabled',type:'switch',label:'Автоматическая CAPTCHA',description:'Смысловая картинка и 3×3 кнопки',icon:'i-captcha'},
      {key:'account_risk_filter_enabled',type:'switch',label:'Риск аккаунта',description:'Проверяет базовые признаки нового профиля',icon:'i-id',premium:true},
      {key:'suspicious_profile_filter_enabled',type:'switch',label:'Подозрительные профили',description:'Анализирует поведение новичка',icon:'i-suspicious',premium:true},
      {key:'auto_quarantine_enabled',type:'switch',label:'Автокарантин',description:'Ограничивает подозрительных новичков',icon:'i-quarantine',premium:true},
      {key:'newcomer_media_filter_enabled',type:'switch',label:'Медиа новичков',description:'Запрещает медиа в первые часы после вступления',icon:'i-image-lock',premium:true},
      {key:'raid_lockdown_enabled',type:'switch',label:'Защита от рейда',description:'Закрывает чат при массовом вступлении',icon:'i-raid-lock',premium:true},
    ]},
    {id:'automatic-punishment',title:'Автоматические наказания',icon:'i-stairs',keywords:'предупреждение очистка лестница адаптивная закрытие ночь',automod:true,fields:[
      {key:'auto_warn_enabled',type:'switch',label:'Автопредупреждения',description:'Добавляет предупреждение после нарушения',icon:'i-auto-warn'},
      {key:'auto_cleanup_enabled',type:'switch',label:'Очистка истории',description:'Пытается удалить недавние сообщения при нарушении',icon:'i-broom'},
      {key:'punishment_ladder_enabled',type:'switch',label:'Лестница наказаний',description:'Предупреждение → мут → бан',icon:'i-stairs',premium:true},
      {key:'adaptive_protection_enabled',type:'switch',label:'Адаптивная защита',description:'Усиливает фильтры при серии нарушений',icon:'i-adaptive',premium:true},
      {key:'auto_chat_close_enabled',type:'switch',label:'Автозакрытие чата',description:'Закрывает отправку сообщений во время атаки',icon:'i-lock',premium:true},
      {key:'night_protection_enabled',type:'switch',label:'Ночная защита',description:'Использует усиленные лимиты ночью',icon:'i-clock',premium:true},
    ]},
    {id:'punishments',title:'Сроки наказаний',icon:'i-timer',keywords:'срок мут бан карантин медиа ссылки команды причина',fields:[
      {key:'default_mute_seconds',type:'duration',label:'Мут по умолчанию'},
      {key:'default_ban_seconds',type:'duration',label:'Бан по умолчанию'},
      {key:'default_quarantine_seconds',type:'duration',label:'Карантин по умолчанию'},
      {key:'default_restrict_media_seconds',type:'duration',label:'Запрет медиа по умолчанию'},
      {key:'default_restrict_links_seconds',type:'duration',label:'Запрет ссылок по умолчанию'},
      {key:'default_restrict_commands_seconds',type:'duration',label:'Блокировка команд по умолчанию'},
      {key:'default_reason',type:'text',label:'Причина по умолчанию'},
      {key:'warn_threshold',type:'number',label:'Порог предупреждений',min:1,max:20},
    ]},
    {id:'limits',title:'Лимиты фильтров',icon:'i-activity',keywords:'лимит окно сообщения капс эмодзи строки длина',fields:[
      {key:'flood_limit',type:'number',label:'Лимит сообщений',min:3,max:50},
      {key:'flood_window_seconds',type:'number',label:'Окно антифлуда, секунд',min:3,max:300},
      {key:'duplicate_limit',type:'number',label:'Повторов до удаления',min:2,max:20},
      {key:'line_limit',type:'number',label:'Максимум строк',min:2,max:200},
      {key:'max_message_length',type:'number',label:'Максимальная длина сообщения',min:100,max:4096},
      {key:'emoji_limit',type:'number',label:'Максимум эмодзи',min:3,max:200},
      {key:'hashtag_limit',type:'number',label:'Максимум хэштегов',min:1,max:100},
      {key:'mass_mentions_limit',type:'number',label:'Максимум упоминаний',min:1,max:100},
    ]},
    {id:'domains',title:'Домены и слова',icon:'i-document',keywords:'домены слова список запрещенные файлы',fields:[
      {key:'allowed_domains',type:'textarea-list',label:'Разрешённые домены'},
      {key:'blocked_words',type:'textarea-list',label:'Запрещённые слова'},
      {key:'dangerous_extensions',type:'textarea-list',label:'Опасные расширения файлов'},
      {key:'symbol_replacement_check',type:'switch',label:'Учитывать замену букв символами'},
    ]},
    {id:'game',title:'Игровые функции',icon:'i-gamepad',keywords:'игра опыт монеты ранги rp',fields:[
      {key:'rp_enabled',type:'switch',label:'Включить RP-команды'},
      {key:'ranks_enabled',type:'switch',label:'Включить XP и ранги'},
      {key:'economy_enabled',type:'switch',label:'Включить AniCoin'},
      {key:'xp_per_message',type:'number',label:'XP за сообщение',min:0,max:100},
      {key:'coins_per_message',type:'number',label:'AniCoin за сообщение',min:0,max:100},
    ]},
  ];

  function applyTelegramChrome() {
    if (!tg) return;
    tg.ready();
    tg.expand();
    try { tg.setHeaderColor('bg_color'); } catch (_) {}
    try { tg.setBackgroundColor('bg_color'); } catch (_) {}
    try { tg.setBottomBarColor('secondary_bg_color'); } catch (_) {}
    try { tg.disableVerticalSwipes(); } catch (_) {}
    const updateTheme = () => document.documentElement.dataset.theme = tg.colorScheme || 'light';
    updateTheme();
    tg.onEvent?.('themeChanged', updateTheme);
    tg.BackButton?.onClick(() => setView('overview'));
  }

  function haptic(kind = 'light') {
    try { tg?.HapticFeedback?.impactOccurred(kind); } catch (_) {}
  }

  async function api(path, options = {}) {
    const headers = new Headers(options.headers || {});
    if (!(options.body instanceof FormData)) headers.set('Content-Type', 'application/json');
    if (tg?.initData) headers.set('X-Telegram-Init-Data', tg.initData);
    else {
      const devUserId = new URLSearchParams(location.search).get('dev_user_id');
      if (devUserId) headers.set('X-Dev-User-Id', devUserId);
    }
    const response = await fetch(path, {...options, headers});
    if (!response.ok) {
      let detail = `Ошибка ${response.status}`;
      try { detail = (await response.json()).detail || detail; } catch (_) {}
      throw new Error(detail);
    }
    return response.status === 204 ? null : response.json();
  }

  function notify(message, error = false) {
    const node = $('#notice');
    node.textContent = message;
    node.classList.remove('hidden');
    node.style.borderColor = error ? '#b94d5e' : '';
    clearTimeout(notify.timer);
    notify.timer = setTimeout(() => node.classList.add('hidden'), 4000);
    tg?.HapticFeedback?.notificationOccurred(error ? 'error' : 'success');
  }

  function currentChat() {
    return state.chats.find(chat => chat.id === state.chatId);
  }

  function premiumAvailable() {
    return Boolean(state.premiumStatus?.active || currentChat()?.premium || state.dashboard?.chat?.premium);
  }

  function setView(view) {
    state.currentView = view;
    $$('.view').forEach(panel => panel.classList.toggle('hidden', panel.dataset.panel !== view));
    $$('.nav[data-view]').forEach(button => button.classList.toggle('active', button.dataset.view === view));
    const bottomMap = {
      overview:'overview', moderation:'moderation', commands:'commands', profile:'profile',
      members:'profile', reports:'profile', logs:'profile', settings:'profile', premium:'profile', admin:'profile',
      custom:'commands', game:'commands', rp:'commands',
    };
    $$('#bottom-nav .nav').forEach(button => button.classList.toggle('active', button.dataset.view === (bottomMap[view] || 'overview')));
    if (view === 'admin' && state.user?.is_bot_admin) loadAdmin();
    if (tg?.BackButton) {
      if (['overview','moderation','commands','profile'].includes(view)) tg.BackButton.hide();
      else tg.BackButton.show();
    }
    haptic('light');
    window.scrollTo({top:0, behavior:'smooth'});
  }

  async function init() {
    try {
      applyTelegramChrome();
      bindEvents();
      state.user = await api('/api/me');
      $('#user-line').textContent = `${state.user.username ? '@' + state.user.username : state.user.first_name}${state.user.premium ? ' · Premium' : ''}`;
      if (state.user.blocked && !state.user.is_bot_admin) throw new Error(state.user.block_reason || 'Доступ к AniGuard заблокирован');
      if (state.user.is_bot_admin) $$('.bot-admin-only').forEach(node => node.classList.remove('hidden'));
      [state.chats, state.plans] = await Promise.all([api('/api/chats'), api('/api/premium/plans')]);
      renderChatSelect();
      renderActions();
      renderSettingsStructure();
      renderAutomodStructure();
      renderMenuLinks();
      renderPlans();
      $('#workspace').classList.remove('hidden');
      $('#bottom-nav').classList.remove('hidden');
      if (state.chats.length) {
        state.chatId = state.chats[0].id;
        $('#chat-select').value = String(state.chatId);
        await loadChat();
      } else if (state.user.is_bot_admin) {
        $('#chat-select').innerHTML = '<option>Нет бесед</option>';
        setView('admin');
      } else {
        $('#workspace').classList.add('hidden');
        $('#bottom-nav').classList.add('hidden');
        $('#empty-state').classList.remove('hidden');
      }
    } catch (error) {
      $('#empty-state').classList.remove('hidden');
      $('#empty-state h1').textContent = 'Не удалось открыть Mini App';
      $('#empty-state p').textContent = error.message;
      notify(error.message, true);
    }
  }

  function chatInitials(title) {
    const parts = String(title || 'AniGuard').trim().split(/\s+/).filter(Boolean);
    return parts.slice(0, 2).map(part => part[0]).join('').toUpperCase() || 'AG';
  }

  function chatAvatarHtml(chat, className = 'chat-avatar') {
    const title = chat?.title || 'AniGuard';
    const image = chat?.avatar_url
      ? `<img src="${escapeHtml(chat.avatar_url)}" alt="Аватар ${escapeHtml(title)}" loading="lazy" onerror="this.remove()">`
      : '';
    return `<span class="${className}"><span>${escapeHtml(chatInitials(title))}</span>${image}</span>`;
  }

  function setChatAvatar(node, chat) {
    if (!node || !chat) return;
    node.innerHTML = `<span>${escapeHtml(chatInitials(chat.title))}</span>${chat.avatar_url ? `<img src="${escapeHtml(chat.avatar_url)}" alt="Аватар ${escapeHtml(chat.title || '')}" onerror="this.remove()">` : ''}`;
  }

  function renderChatSelect() {
    $('#chat-select').innerHTML = state.chats.length
      ? state.chats.map(chat => `<option value="${chat.id}">${escapeHtml(chat.title)}${chat.premium ? ' · Premium' : ''}</option>`).join('')
      : '<option>Нет бесед</option>';
    const options = $('#group-options');
    if (options) {
      options.innerHTML = state.chats.length ? state.chats.map(chat => `
        <button class="group-option pressable" type="button" data-chat-id="${chat.id}">
          ${chatAvatarHtml(chat)}
          <span><b>${escapeHtml(chat.title)}</b><small>${chat.username ? '@' + escapeHtml(chat.username) + ' · ' : ''}${chat.premium ? 'Premium активен' : 'Обычный доступ'}</small></span>
          <span class="check">${chat.id === state.chatId ? '✓' : ''}</span>
        </button>`).join('') : '<p class="muted">Доступных групп нет.</p>';
    }
    const chat = currentChat();
    if (chat) {
      setChatAvatar($('#current-chat-avatar'), chat);
      $('#current-chat-name').textContent = chat.title;
      const members = state.profile?.members ?? state.dashboard?.metrics?.members;
      $('#current-chat-meta').textContent = `${members ?? '—'} участников${premiumAvailable() ? ' · Premium' : ''}`;
      $('#overview-chat-title').textContent = chat.title;
      $('#overview-chat-subtitle').textContent = '';
    }
  }

  async function loadChat() {
    if (!state.chatId) return;
    try {
      const [dashboard, profile, members, logs, reports, rp, rules, custom, game, basicCommands, groupRules, welcome, captcha, premiumStatus] = await Promise.all([
        api(`/api/chats/${state.chatId}/dashboard`),
        api(`/api/chats/${state.chatId}/profile`),
        api(`/api/chats/${state.chatId}/members?limit=500`),
        api(`/api/chats/${state.chatId}/logs?limit=100`),
        api(`/api/chats/${state.chatId}/reports`),
        api(`/api/chats/${state.chatId}/rp`),
        api(`/api/chats/${state.chatId}/rules`),
        api(`/api/chats/${state.chatId}/custom-commands`),
        api(`/api/chats/${state.chatId}/game-commands`),
        api(`/api/chats/${state.chatId}/basic-commands`),
        api(`/api/chats/${state.chatId}/group-rules`),
        api(`/api/chats/${state.chatId}/welcome`),
        api(`/api/chats/${state.chatId}/captcha`),
        api(`/api/chats/${state.chatId}/premium/status`),
      ]);
      state.dashboard = dashboard;
      state.profile = profile;
      state.logs = logs;
      state.members = members;
      state.settings = structuredClone(dashboard.settings);
      state.customCommands = custom;
      state.gameCommands = game;
      state.basicCommands = basicCommands.commands || [];
      state.rulesConfig = groupRules;
      state.welcome = welcome;
      state.captcha = captcha;
      state.premiumStatus = premiumStatus;
      const chat = currentChat();
      if (chat) {
        chat.premium = dashboard.chat.premium;
        chat.premium_until = dashboard.chat.premium_until;
      }
      renderMetrics();
      renderBasicCommands();
      renderMembers();
      renderLogs(logs);
      renderFullLogs(logs);
      renderProfile();
      renderReports(reports);
      renderRp(rp);
      renderRules(rules);
      renderCustom(custom);
      renderGame(game);
      renderSettingsValues();
      renderAutomodValues();
      updatePremiumStatus();
      renderPremiumDetails();
      renderChatSelect();
      $('#chat-select').value = String(state.chatId);
    } catch (error) {
      notify(error.message, true);
    }
  }

  function renderMetrics() {
    const m = state.dashboard.metrics;
    const rows = [
      ['i-users','Участники',state.profile?.members ?? m.members], ['i-activity','Действия сегодня',m.actions_today],
      ['i-flag','Жалобы',m.open_reports], ['i-spark','Кастомные',m.custom_commands],
      ['i-gamepad','Игровые',m.game_commands], ['i-rule','Правила',m.rules],
    ];
    $('#metrics').innerHTML = rows.map(([ic,label,value]) => `<div class="metric">${iconSmall(ic)}<span>${label}</span><strong>${value}</strong></div>`).join('');
    $('#overview-status').textContent = state.settings.chat_locked ? 'Чат закрыт' : 'Защита активна';
  }

  function renderActions() {
    const card = action => `<button type="button" class="action ${action.premium ? 'premium' : ''}" data-action="${action.action}" data-search="${escapeHtml((action.title + ' ' + action.description).toLowerCase())}">${iconSmall(action.icon)}<b>${escapeHtml(action.title)}</b><small>${escapeHtml(action.description)}${action.premium ? ' · Premium' : ''}</small></button>`;
    $('#regular-actions').innerHTML = actionDefinitions.map(card).join('');
    $('#quick-actions').innerHTML = actionDefinitions.filter(item => ['warn','mute','ban','restrict_media','restrict_links','quarantine'].includes(item.action)).map(card).join('');
  }


  function initialsOf(user) {
    return [user?.first_name, user?.last_name].filter(Boolean).map(value => String(value)[0]).join('').slice(0, 2).toUpperCase() || 'AG';
  }

  function avatarHtml(user, large = false) {
    return `<span class="user-avatar${large ? ' large' : ''}"><span>${escapeHtml(initialsOf(user))}</span>${user?.avatar_url ? `<img src="${escapeHtml(user.avatar_url)}" alt="Аватар ${escapeHtml(user.full_name || user.first_name || '')}" loading="lazy">` : ''}</span>`;
  }

  function renderBasicCommands() {
    const node = $('#basic-commands-list');
    if (!node) return;
    $('#basic-editor-status').textContent = premiumAvailable() ? 'РЕДАКТОР PREMIUM АКТИВЕН' : 'РЕДАКТОР PREMIUM ЗАБЛОКИРОВАН';
    node.innerHTML = state.basicCommands.length ? state.basicCommands.map(command => {
      const action = actionDefinitions.find(item => item.action === command.action);
      return `<div class="telegram-row basic-command-row" data-basic-key="${escapeHtml(command.key)}">
        <span class="row-icon"><svg><use href="#${action?.icon || 'i-command-panel'}"></use></svg></span>
        <span class="row-copy"><b>${escapeHtml(command.name)}</b><small>Вызов команды: ${escapeHtml(command.trigger)}</small></span>
        <span class="basic-command-actions">
          <button class="secondary basic-run" type="button" aria-label="Выполнить"><svg><use href="#i-play"></use></svg></button>
          <button class="secondary basic-edit" type="button" aria-label="Редактировать"><svg><use href="#i-edit"></use></svg></button>
        </span>
      </div>`;
    }).join('') : '<p class="muted">Основные команды недоступны.</p>';
  }

  function renderMembers(filter = '') {
    $('#action-target').innerHTML = state.members.length
      ? state.members.map(user => `<option value="${user.id}">${escapeHtml(user.full_name || user.first_name)}${user.username ? ` (@${escapeHtml(user.username)})` : ''}</option>`).join('')
      : '<option value="">Нет известных участников</option>';
    const list = $('#members-list');
    if (!list) return;
    const query = String(filter || '').trim().toLowerCase();
    const rows = state.members.filter(user => !query || `${user.full_name || ''} ${user.id} ${user.username || ''}`.toLowerCase().includes(query));
    $('#members-count').textContent = `${rows.length} из ${state.members.length} участников`;
    list.innerHTML = rows.length ? rows.map(user => {
      const protectedUser = ['owner','admin'].includes(user.role);
      const username = user.username ? `@${escapeHtml(user.username)}` : 'Юзернейм отсутствует';
      return `<article class="member-row" data-member-id="${user.id}">
        <div class="member-identity">${avatarHtml(user)}<div class="user-copy"><b>${escapeHtml(user.full_name || user.first_name || 'Без имени')}</b><small>ID: ${user.id}</small><small>${username}</small></div></div>
        <div class="member-buttons">
          <button class="danger member-ban" type="button" ${protectedUser ? 'disabled' : ''}>Заблокировать</button>
          <button class="secondary member-kick" type="button" ${protectedUser ? 'disabled' : ''}>Удалить</button>
          <button class="secondary member-info" type="button">Информация</button>
        </div>
      </article>`;
    }).join('') : '<div class="card muted">Участники не найдены.</div>';
  }


  function renderLogs(logs) {
    $('#overview-logs').innerHTML = logs.length
      ? logs.slice(0, 12).map(row => `<div class="list-row"><b>${escapeHtml(row.action)}</b><p>${escapeHtml(row.reason || 'Без причины')} · ${new Date(row.created_at).toLocaleString('ru-RU')}</p></div>`).join('')
      : '<p class="muted">Журнал пока пуст.</p>';
  }

  function renderFullLogs(logs) {
    const node = $('#full-logs');
    if (!node) return;
    node.innerHTML = logs.length
      ? logs.map(row => `<article class="list-card"><div>${icon('i-log')}<h3>${escapeHtml(row.action)}</h3><p>${escapeHtml(row.reason || 'Без причины')}</p><p>${new Date(row.created_at).toLocaleString('ru-RU')}${row.target_id ? ` · User ID: ${row.target_id}` : ''}</p></div></article>`).join('')
      : '<article class="card muted">Журнал пока пуст.</article>';
  }

  function profileRow(iconId, title, subtitle, value = '', action = '') {
    return `<button class="telegram-row pressable profile-click-row" type="button" ${action ? `data-profile-action="${action}"` : ''}><span class="row-icon"><svg><use href="#${iconId}"></use></svg></span><span class="row-copy"><b>${escapeHtml(title)}</b><small>${escapeHtml(subtitle)}</small></span>${value ? `<span class="muted">${escapeHtml(value)}</span>` : '<svg class="chevron"><use href="#i-chevron-right"></use></svg>'}</button>`;
  }

  function quickSettingRow(key, iconId, title, subtitle) {
    return `<label class="telegram-row"><span class="row-icon"><svg><use href="#${iconId}"></use></svg></span><span class="row-copy"><b>${escapeHtml(title)}</b><small>${escapeHtml(subtitle)}</small></span><span class="toggle"><input type="checkbox" data-quick-setting="${key}"><span></span></span></label>`;
  }

  function renderProfile() {
    if (!state.profile) return;
    const profile = state.profile;
    setChatAvatar($('#profile-avatar'), {...profile, avatar_url: profile.avatar_url || currentChat()?.avatar_url});
    $('#profile-title').textContent = profile.title;
    $('#profile-username').textContent = profile.username ? `@${profile.username} · Telegram-группа` : `ID: ${profile.id}`;
    const owner = profile.owner;
    const ownerName = owner ? (owner.full_name || owner.first_name || `ID ${owner.id}`) : 'Не определён';
    const premiumSubtitle = state.premiumStatus?.source === 'owner'
      ? 'Получен автоматически от Premium создателя группы'
      : profile.premium ? 'Premium куплен для группы' : 'Premium не активен';
    $('#profile-info-list').innerHTML = [
      profileRow('i-users', 'Участники', 'Открыть полный список участников', String(profile.members ?? state.members.length), 'members'),
      profileRow('i-crown', 'Владелец', 'Открыть данные создателя группы', ownerName, 'owner'),
      profileRow('i-diamond', 'AniGuard Premium', premiumSubtitle, profile.premium ? 'Активен' : 'Неактивен', 'premium'),
      profileRow('i-id', 'Telegram ID', 'Идентификатор выбранной группы', String(profile.id)),
    ].join('');
    $('#profile-quick-settings').innerHTML = [
      profileRow('i-rule', 'Правила группы', 'Создать или изменить опубликованные правила', `${state.rulesConfig?.rules?.length || 0}`, 'rules'),
      profileRow('i-message-star', 'Приветственное сообщение', state.welcome?.photo_name ? 'Текст и фотография добавлены' : 'Текст и фотография для новичков', state.welcome?.enabled ? 'Включено' : 'Выключено', 'welcome'),
      profileRow('i-captcha', 'CAPTCHA новых участников', 'Смысловая картинка и клавиатура 3×3', state.captcha?.enabled ? 'Включена' : 'Выключена', 'captcha'),
      quickSettingRow('anti_flood_enabled', 'i-filter', 'Антифлуд', 'Контроль частых сообщений'),
      quickSettingRow('link_filter_enabled', 'i-link-lock', 'Фильтр ссылок', 'Ограничение ссылок новичков'),
    ].join('');
    $$('[data-quick-setting]').forEach(input => { input.checked = Boolean(state.settings[input.dataset.quickSetting]); });
    $('#profile-open-link').disabled = !(profile.username || profile.invite_link);
  }


  function renderAutomodStructure() {
    const node = $('#automod-sections');
    if (!node) return;
    node.innerHTML = settingGroups.filter(group => group.automod).map(group => `
      <section class="automod-group" data-automod-group="${group.id}">
        <h2>${escapeHtml(group.title)}</h2>
        <p>Автоматические функции выбранной группы</p>
        <div class="telegram-list">
          ${group.fields.map(field => `
            <label class="automod-row" data-search="${escapeHtml((field.label + ' ' + field.description).toLowerCase())}">
              <span class="row-icon"><svg><use href="#${field.icon}"></use></svg></span>
              <span class="row-copy"><span class="automod-title-line"><span class="automod-title">${escapeHtml(field.label)}</span>${field.premium ? '<span class="premium-chip">PREMIUM</span>' : ''}</span><span class="automod-description">${escapeHtml(field.description)}</span></span>
              <span class="toggle"><input type="checkbox" data-automod-key="${field.key}" data-premium="${field.premium ? 'true' : 'false'}"><span></span></span>
            </label>`).join('')}
        </div>
      </section>`).join('');
  }

  function renderAutomodValues() {
    $$('[data-automod-key]').forEach(input => {
      input.checked = Boolean(state.settings[input.dataset.automodKey]);
    });
    $('#moderation-premium-state').textContent = premiumAvailable() ? 'Premium активен' : 'Обычный доступ';
  }

  function renderMenuLinks() {
    const node = $('#menu-links');
    if (!node) return;
    const links = [
      ['members','i-users','Участники и роли','Поиск и права модераторов'],
      ['custom','i-spark','Кастомные команды','Premium-конструктор'],
      ['game','i-gamepad','Игровые команды','XP, AniCoin и награды'],
      ['reports','i-flag','Жалобы','Обращения пользователей'],
      ['rp','i-chat','RP-команды','Ролевые действия'],
      ['logs','i-document','Журнал действий','История модерации'],
      ['settings','i-gear','Настройки группы','Все параметры'],
      ['premium','i-diamond','AniGuard Premium','Тарифы и возможности'],
    ];
    node.innerHTML = links.map(([view,ic,title,sub]) => `<button class="telegram-row menu-view-link pressable" data-view-target="${view}" type="button"><span class="row-icon"><svg><use href="#${ic}"></use></svg></span><span class="row-copy"><b>${title}</b><small>${sub}</small></span><svg class="chevron"><use href="#i-chevron-right"></use></svg></button>`).join('');
  }

  async function saveSettingKey(key, value, sourceInput = null) {
    const previous = state.settings[key];
    state.settings[key] = value;
    try {
      const result = await api(`/api/chats/${state.chatId}/settings`, {
        method:'PUT',
        body:JSON.stringify({settings:{[key]:value}}),
      });
      state.settings = {...state.settings, ...result.settings};
      renderAutomodValues();
      renderProfile();
      notify('Настройка сохранена.');
    } catch (error) {
      state.settings[key] = previous;
      if (sourceInput) sourceInput.checked = Boolean(previous);
      if (/Premium/i.test(error.message)) openPremiumDialog(error.message);
      else notify(error.message, true);
    }
  }

  function openPremiumDialog(message = 'Эта функция доступна только с AniGuard Premium.') {
    $('#premium-dialog-text').textContent = message;
    $('#premium-dialog').showModal();
  }

  function renderReports(reports) {
    $('#reports-list').innerHTML = reports.length ? reports.map(report => `<article class="list-card" data-report-id="${report.id}"><div>${icon('i-report')}<h3>Жалоба AG-${report.id}</h3><p>User ID: ${report.target_id} · ${escapeHtml(report.reason)} · ${new Date(report.created_at).toLocaleString('ru-RU')}</p></div><div class="actions"><button class="secondary report-decision" data-decision="dismiss">Отклонить</button><button class="secondary report-decision" data-decision="warn">Пред</button><button class="secondary report-decision" data-decision="mute">Мут</button><button class="danger report-decision" data-decision="ban">Бан</button></div></article>`).join('') : '<article class="card muted">Открытых жалоб нет.</article>';
  }

  function renderRp(rows) {
    $('#rp-list').innerHTML = rows.length ? rows.map(row => `<article class="list-card" data-rp-id="${row.id}"><div>${icon(row.is_premium ? 'i-premium' : 'i-rp')}<h3>${escapeHtml(row.name)}</h3><p>${escapeHtml(row.response_template)} · кулдаун ${row.cooldown_seconds} сек.</p></div><div class="actions"><button class="secondary rp-toggle" data-enabled="${row.enabled}">${row.enabled ? 'Отключить' : 'Включить'}</button><button class="danger rp-delete">Удалить</button></div></article>`).join('') : '<article class="card muted">RP-команд пока нет.</article>';
  }

  function renderRules(rows) {
    $('#rules-list').innerHTML = rows.length ? rows.map(row => `<article class="list-card" data-rule-id="${row.id}"><div>${icon(row.is_premium ? 'i-premium' : 'i-shield')}<h3>${escapeHtml(row.name)}</h3><p>${escapeHtml(JSON.stringify(row.condition))} → ${escapeHtml(JSON.stringify(row.actions))}</p></div><div class="actions"><button class="secondary rule-toggle" data-enabled="${row.enabled}">${row.enabled ? 'Отключить' : 'Включить'}</button><button class="danger rule-delete">Удалить</button></div></article>`).join('') : '<article class="card muted">Правила ещё не созданы.</article>';
  }

  function formatDuration(seconds) {
    if (seconds === 0) return 'навсегда';
    if (seconds == null) return 'по настройкам группы';
    const units = [[2592000,'мес'],[604800,'нед'],[86400,'д'],[3600,'ч'],[60,'мин'],[1,'сек']];
    let left = Number(seconds), parts = [];
    for (const [size,label] of units) {
      if (left >= size) { const count = Math.floor(left / size); left %= size; parts.push(`${count} ${label}`); }
      if (parts.length === 2) break;
    }
    return parts.join(' ') || '0 сек.';
  }

  function renderCustom(rows) {
    $('#custom-list').innerHTML = rows.length ? rows.map(row => `<article class="list-card" data-custom-id="${row.id}"><div>${icon('i-premium')}<h3>${escapeHtml(row.name)} <span class="muted">— ${escapeHtml(row.trigger)}</span></h3><p>${escapeHtml(row.action_type)} · ${formatDuration(row.duration_seconds)}${row.frozen ? ' · заморожена' : ''}</p><p>${escapeHtml(row.response_template)}</p></div><div class="actions"><button class="secondary custom-edit" ${row.frozen ? 'disabled' : ''}>Редактировать</button><button class="secondary custom-toggle" data-enabled="${row.enabled}" ${row.frozen ? 'disabled' : ''}>${row.enabled ? 'Отключить' : 'Включить'}</button><button class="danger custom-delete">Удалить</button></div></article>`).join('') : '<article class="card muted">Кастомных команд пока нет.</article>';
  }

  function renderGame(rows) {
    $('#game-list').innerHTML = rows.length ? rows.map(row => `<article class="list-card" data-game-id="${row.id}"><div>${icon('i-game')}<h3>${escapeHtml(row.name)} <span class="muted">— ${escapeHtml(row.trigger)}</span></h3><p>${escapeHtml(row.command_type)} · +${row.reward_xp} XP · +${row.reward_coins} AniCoin · кулдаун ${row.cooldown_seconds} сек.</p></div><div class="actions"><button class="secondary game-edit">Редактировать</button><button class="secondary game-toggle" data-enabled="${row.enabled}">${row.enabled ? 'Отключить' : 'Включить'}</button><button class="danger game-delete">Удалить</button></div></article>`).join('') : '<article class="card muted">Игровых команд пока нет.</article>';
  }

  function fieldHtml(field) {
    const premium = field.premium ? ' data-premium-setting="true"' : '';
    const label = `${escapeHtml(field.label)}${field.premium ? ' <span class="premium-chip">PREMIUM</span>' : ''}`;
    if (field.type === 'switch') return `<label class="switch-line"><input id="setting-${field.key}" data-key="${field.key}"${premium} type="checkbox"><span>${label}</span></label>`;
    if (field.type === 'textarea-list') return `<label>${label}<textarea id="setting-${field.key}" data-key="${field.key}"${premium} data-type="list" class="control" rows="4"></textarea></label>`;
    if (field.type === 'duration') return `<label>${label}<input id="setting-${field.key}" data-key="${field.key}"${premium} data-type="duration" class="control" placeholder="Например: 7 дней или навсегда"></label>`;
    if (field.type === 'number') return `<label>${label}<input id="setting-${field.key}" data-key="${field.key}"${premium} class="control" type="number" min="${field.min}" max="${field.max}"></label>`;
    return `<label>${label}<input id="setting-${field.key}" data-key="${field.key}"${premium} class="control"></label>`;
  }

  function renderSettingsStructure() {
    $('#settings-list').innerHTML = settingGroups.map(group => `<details class="setting-group" data-search="${escapeHtml(group.keywords)}"><summary><div class="section-title">${icon(group.icon)}<div><h2>${escapeHtml(group.title)}</h2><p>${group.premium ? 'Premium-модуль' : 'Открыть настройки'}</p></div></div></summary><div class="accordion-body">${group.fields.map(fieldHtml).join('')}</div></details>`).join('');
  }

  function renderSettingsValues() {
    for (const group of settingGroups) {
      for (const field of group.fields) {
        const node = $(`#setting-${field.key}`);
        if (!node) continue;
        const value = state.settings[field.key];
        if (field.type === 'switch') node.checked = Boolean(value);
        else if (field.type === 'textarea-list') node.value = Array.isArray(value) ? value.join('\n') : '';
        else if (field.type === 'duration') node.value = formatDuration(value);
        else node.value = String(value ?? '');
      }
    }
  }

  function readSettings() {
    for (const node of $$('[data-key]', $('#settings-list'))) {
      const key = node.dataset.key;
      if (node.type === 'checkbox') state.settings[key] = node.checked;
      else if (node.dataset.type === 'list') state.settings[key] = node.value.split('\n').map(v => v.trim()).filter(Boolean);
      else if (node.dataset.type === 'duration') state.settings[key] = parseDurationText(node.value);
      else if (node.type === 'number' || node.tagName === 'SELECT') state.settings[key] = Number(node.value);
      else state.settings[key] = node.value;
    }
  }

  function renderPlans() {
    const label = state.premiumStatus?.active ? 'Продлить' : 'Купить';
    $('#plans-list').innerHTML = state.plans.map(plan => `<article class="plan ${plan.code === 'season' ? 'recommended' : ''}"><span class="badge">${escapeHtml(plan.badge)}</span><h2>${escapeHtml(plan.title)}</h2><div class="price">${plan.stars} Stars</div><p>${escapeHtml(plan.description)}</p><button class="primary buy-plan" data-plan="${plan.code}">${label} на ${plan.days} дней</button></article>`).join('');
  }

  function formatRemaining(seconds) {
    if (seconds == null) return 'Бессрочно';
    if (seconds <= 0) return 'Истёк';
    const days = Math.floor(seconds / 86400);
    const hours = Math.floor((seconds % 86400) / 3600);
    return days ? `${days} д. ${hours} ч.` : `${hours} ч.`;
  }

  function updatePremiumStatus() {
    const status = state.premiumStatus;
    if (!status) return;
    if (status.source === 'owner') $('#premium-status').textContent = 'Группа автоматически получила Premium от создателя';
    else if (status.active) $('#premium-status').textContent = status.lifetime ? 'Premium группы активен бессрочно' : `Premium группы активен до ${new Date(status.until).toLocaleString('ru-RU')}`;
    else $('#premium-status').textContent = 'Premium не активен';
  }

  function renderPremiumDetails() {
    const status = state.premiumStatus;
    if (!status) return;
    const sourceNames = {owner:'Premium создателя',group:'Premium группы',user:'Premium администратора',none:'Не активен'};
    $('#premium-details').innerHTML = [
      ['Статус', status.active ? 'Активен' : 'Неактивен'],
      ['Источник', sourceNames[status.source] || status.source],
      ['Осталось', status.active ? formatRemaining(status.remaining_seconds) : '—'],
    ].map(([label,value]) => `<div class="premium-detail"><small>${escapeHtml(label)}</small><b>${escapeHtml(value)}</b></div>`).join('');
    const ownerNote = status.source === 'owner' && status.owner
      ? `<div class="owner-source-note">Premium действует, пока подписка создателя ${escapeHtml(status.owner.full_name || status.owner.first_name)} активна.</div>` : '';
    $('#premium-details').insertAdjacentHTML('beforeend', ownerNote);
    $('#premium-features').innerHTML = (status.features || []).map(feature => `<span>${escapeHtml(feature)}</span>`).join('');
    renderPlans();
  }


  function openAction(name) {
    const action = actionDefinitions.find(item => item.action === name);
    if (!action) return;
    if (action.premium && !premiumAvailable()) { openPremiumDialog('Для действия «' + action.title + '» нужен AniGuard Premium.'); return; }
    state.currentAction = action;
    $('#action-title').textContent = action.title;
    $('#action-description').textContent = action.description;
    $('#action-icon use').setAttribute('href', `#${action.icon}`);
    $('#action-target-row').classList.toggle('hidden', !action.target);
    $('#action-duration-row').classList.toggle('hidden', !action.duration);
    $('#action-amount-row').classList.toggle('hidden', !action.amount);
    $('#action-amount').value = action.action === 'slow' ? 15 : 25;
    $('#action-duration').value = '';
    $('#action-reason').value = '';
    $('#action-dialog').showModal();
  }

  async function executeAction() {
    const action = state.currentAction;
    if (!action || !state.chatId) return;
    const body = {action:action.action, reason:$('#action-reason').value};
    if (action.target) body.target_id = Number($('#action-target').value);
    if (action.duration && $('#action-duration').value.trim()) body.duration_seconds = parseDurationText($('#action-duration').value);
    if (action.amount) body.amount = Number($('#action-amount').value);
    try {
      await api(`/api/chats/${state.chatId}/actions`, {method:'POST', body:JSON.stringify(body)});
      $('#action-dialog').close(); notify(`Команда «${action.title}» выполнена.`); await loadChat();
    } catch (error) { notify(error.message, true); }
  }

  function parseDurationText(value) {
    const text = value.trim().toLowerCase();
    if (['навсегда','бессрочно','постоянно'].includes(text)) return 0;
    const regex = /(\d+)\s*(секунд(?:а|ы)?|сек|с|минут(?:а|ы)?|мин|м|час(?:а|ов)?|ч|день|дня|дней|д|неделя|недели|недель|нед|месяц|месяца|месяцев|мес)\.?/g;
    const multipliers = {с:1,сек:1,секунда:1,секунды:1,секунд:1,м:60,мин:60,минута:60,минуты:60,минут:60,ч:3600,час:3600,часа:3600,часов:3600,д:86400,день:86400,дня:86400,дней:86400,нед:604800,неделя:604800,недели:604800,недель:604800,мес:2592000,месяц:2592000,месяца:2592000,месяцев:2592000};
    let total = 0, match, consumed = '';
    while ((match = regex.exec(text))) { total += Number(match[1]) * multipliers[match[2]]; consumed += match[0]; }
    if (!total || consumed.replace(/\s/g,'').length !== text.replace(/\s/g,'').length) throw new Error('Срок указывается так: 30 секунд, 2 часа, 7 дней, 1 месяц или навсегда');
    if (total > 31536000) throw new Error('Максимальный временный срок — 365 дней');
    return total;
  }

  async function createCustom() {
    try {
      if (!premiumAvailable()) throw new Error('Конструктор кастомных команд доступен с Premium');
      const action = $('#custom-action').value;
      const timedActions = new Set(['mute','ban','quarantine','restrict_media','restrict_links','restrict_commands']);
      const duration = timedActions.has(action) ? parseDurationText($('#custom-duration').value) : null;
      const payload = {
        name:$('#custom-name').value.trim(), trigger:$('#custom-trigger').value.trim(), action_type:action,
        duration_seconds:duration, response_template:$('#custom-template').value.trim(), required_role:$('#custom-role').value,
        target_mode:$('#custom-target').value, cooldown_seconds:Number($('#custom-cooldown').value),
        delete_trigger:$('#custom-delete').checked, require_reason:$('#custom-reason-required').checked, enabled:true,
      };
      const path = state.editingCustomId
        ? `/api/chats/${state.chatId}/custom-commands/${state.editingCustomId}`
        : `/api/chats/${state.chatId}/custom-commands`;
      await api(path, {method:state.editingCustomId ? 'PATCH' : 'POST', body:JSON.stringify(payload)});
      notify(state.editingCustomId ? 'Кастомная команда обновлена.' : 'Кастомная команда создана.');
      state.editingCustomId = null;
      $('#create-custom').textContent = 'Создать кастомную команду';
      await loadChat();
    } catch (error) { notify(error.message, true); }
  }

  function editCustom(id) {
    const row = state.customCommands.find(item => item.id === Number(id));
    if (!row || row.frozen) return;
    state.editingCustomId = row.id;
    $('#custom-name').value = row.name;
    $('#custom-trigger').value = row.trigger;
    $('#custom-action').value = row.action_type;
    $('#custom-duration').value = row.duration_seconds == null ? '7 дней' : formatDuration(row.duration_seconds);
    $('#custom-template').value = row.response_template;
    $('#custom-role').value = row.required_role;
    $('#custom-target').value = row.target_mode;
    $('#custom-cooldown').value = row.cooldown_seconds;
    $('#custom-delete').checked = row.delete_trigger;
    $('#custom-reason-required').checked = row.require_reason;
    $('#create-custom').textContent = 'Сохранить изменения';
    setView('custom');
  }

  async function createGame() {
    const payload = {
      name:$('#game-name').value.trim(), trigger:$('#game-trigger').value.trim(), command_type:$('#game-type').value,
      response_template:$('#game-template').value.trim(), response_variants:$('#game-variants').value.split('\n').map(v => v.trim()).filter(Boolean),
      reward_xp:Number($('#game-xp').value), reward_coins:Number($('#game-coins').value), cooldown_seconds:Number($('#game-cooldown').value),
      access:$('#game-access').value, enabled:true,
    };
    try {
      const path = state.editingGameId
        ? `/api/chats/${state.chatId}/game-commands/${state.editingGameId}`
        : `/api/chats/${state.chatId}/game-commands`;
      await api(path, {method:state.editingGameId ? 'PATCH' : 'POST', body:JSON.stringify(payload)});
      notify(state.editingGameId ? 'Игровая команда обновлена.' : 'Игровая команда добавлена.');
      state.editingGameId = null;
      $('#create-game').textContent = 'Добавить игровую команду';
      await loadChat();
    }
    catch (error) { notify(error.message, true); }
  }

  function editGame(id) {
    const row = state.gameCommands.find(item => item.id === Number(id));
    if (!row) return;
    state.editingGameId = row.id;
    $('#game-name').value = row.name;
    $('#game-trigger').value = row.trigger;
    $('#game-type').value = row.command_type;
    $('#game-template').value = row.response_template;
    $('#game-variants').value = (row.response_variants || []).join('\n');
    $('#game-xp').value = row.reward_xp;
    $('#game-coins').value = row.reward_coins;
    $('#game-cooldown').value = row.cooldown_seconds;
    $('#game-access').value = row.access;
    $('#create-game').textContent = 'Сохранить изменения';
    setView('game');
  }

  async function createRp() {
    const payload = {name:$('#rp-name').value.trim(), aliases:$('#rp-aliases').value.split(',').map(v=>v.trim()).filter(Boolean), response_template:$('#rp-template').value.trim(), response_variants:$('#rp-variants').value.split('\n').map(v=>v.trim()).filter(Boolean), enabled:true, is_premium:$('#rp-premium').checked, cooldown_seconds:Number($('#rp-cooldown').value), access:$('#rp-access').value, reward_xp:Number($('#rp-xp').value), reward_coins:Number($('#rp-coins').value)};
    try { await api(`/api/chats/${state.chatId}/rp`, {method:'POST', body:JSON.stringify(payload)}); notify('RP-команда добавлена.'); await loadChat(); }
    catch (error) { notify(error.message, true); }
  }

  async function createRule() {
    const payload = {name:$('#rule-name').value.trim(), condition:{type:$('#rule-condition').value}, actions:[{type:$('#rule-action').value}], enabled:true, is_premium:$('#rule-premium').checked};
    try { await api(`/api/chats/${state.chatId}/rules`, {method:'POST', body:JSON.stringify(payload)}); notify('Правило создано.'); await loadChat(); }
    catch (error) { notify(error.message, true); }
  }

  async function saveSettings() {
    readSettings();
    try { const result = await api(`/api/chats/${state.chatId}/settings`, {method:'PUT', body:JSON.stringify({settings:state.settings})}); state.settings = result.settings; renderSettingsValues(); notify('Настройки сохранены.'); await loadChat(); }
    catch (error) { notify(error.message, true); }
  }

  async function buyPlan(code) {
    try {
      const result = await api(`/api/chats/${state.chatId}/premium/invoice`, {method:'POST', body:JSON.stringify({plan_code:code})});
      if (!tg?.openInvoice) throw new Error('Оплата доступна только внутри Telegram');
      tg.openInvoice(result.invoice_url, status => { if (status === 'paid') { notify('Оплата прошла. Premium активируется автоматически.'); setTimeout(loadChat, 1200); } else if (status === 'failed') notify('Оплата не прошла.', true); });
    } catch (error) { notify(error.message, true); }
  }


  async function askConfirm(text) {
    if (tg?.showConfirm) return await new Promise(resolve => tg.showConfirm(text, resolve));
    return window.confirm(text);
  }

  async function memberModeration(memberId, action) {
    const member = state.members.find(item => item.id === Number(memberId));
    if (!member) return;
    const verb = action === 'ban' ? 'заблокировать' : 'удалить из группы';
    if (!await askConfirm(`Действительно ${verb} ${member.full_name || member.first_name}?`)) return;
    try {
      const payload = {action, target_id:member.id, reason:'Действие администратора через Mini App'};
      if (action === 'ban') payload.duration_seconds = 0;
      await api(`/api/chats/${state.chatId}/actions`, {method:'POST', body:JSON.stringify(payload)});
      notify(action === 'ban' ? 'Участник заблокирован.' : 'Участник удалён из группы.');
      await loadChat();
    } catch (error) { notify(error.message, true); }
  }

  async function showMemberDetails(memberId) {
    try {
      const member = await api(`/api/chats/${state.chatId}/members/${memberId}`);
      const username = member.username ? `@${member.username}` : 'Юзернейм отсутствует';
      const date = value => value ? new Date(value).toLocaleString('ru-RU') : 'Нет';
      $('#member-details').innerHTML = `<article class="member-details-card">
        ${avatarHtml(member, true)}
        <h2>${escapeHtml(member.full_name || member.first_name || 'Без имени')}</h2>
        <p class="muted">ID: ${member.id}</p><p>${escapeHtml(username)}</p>
        <div class="detail-grid">
          <div class="detail-item"><small>Роль</small><b>${escapeHtml(member.role)}</b></div>
          <div class="detail-item"><small>Статус Telegram</small><b>${escapeHtml(member.telegram?.status || 'неизвестен')}</b></div>
          <div class="detail-item"><small>Предупреждения</small><b>${member.warnings}</b></div>
          <div class="detail-item"><small>Сообщения</small><b>${member.messages}</b></div>
          <div class="detail-item"><small>XP</small><b>${member.xp}</b></div>
          <div class="detail-item"><small>AniCoin</small><b>${member.coins}</b></div>
          <div class="detail-item"><small>Вступил</small><b>${escapeHtml(date(member.joined_at))}</b></div>
          <div class="detail-item"><small>Последняя активность</small><b>${escapeHtml(date(member.last_seen_at))}</b></div>
          <div class="detail-item"><small>Мут до</small><b>${escapeHtml(date(member.muted_until))}</b></div>
          <div class="detail-item"><small>Карантин до</small><b>${escapeHtml(date(member.quarantined_until))}</b></div>
        </div>
        <p class="muted">Активные ограничения: ${escapeHtml((member.active_restrictions || []).join(', ') || 'нет')}</p>
      </article>`;
      $('#member-dialog').showModal();
    } catch (error) { notify(error.message, true); }
  }

  function showOwnerDetails() {
    const owner = state.profile?.owner;
    if (!owner) return notify('Telegram не вернул данные владельца.', true);
    $('#owner-details').innerHTML = `<article class="member-details-card">${avatarHtml(owner, true)}<h2>${escapeHtml(owner.full_name || owner.first_name || 'Без имени')}</h2><p class="muted">ID: ${owner.id}</p><p>${owner.username ? '@' + escapeHtml(owner.username) : 'Юзернейм отсутствует'}</p><div class="owner-source-note">Создатель Telegram-группы. Premium этого аккаунта автоматически распространяется на группу до окончания подписки.</div></article>`;
    $('#owner-dialog').showModal();
  }

  function openBasicEditor(key) {
    if (!premiumAvailable()) return openPremiumDialog('Редактор основных команд доступен только с AniGuard Premium.');
    const command = state.basicCommands.find(item => item.key === key);
    if (!command) return;
    state.editingBasicKey = key;
    $('#basic-command-title').textContent = `Редактирование: ${command.name}`;
    $('#basic-command-trigger').value = command.trigger;
    $('#basic-command-response').value = command.response;
    $('#basic-command-dialog').showModal();
  }

  async function saveBasicCommand() {
    const trigger = $('#basic-command-trigger').value.trim();
    const response = $('#basic-command-response').value.trim();
    if (!trigger || !response) return notify('Введите вызов команды и текст ответа.', true);
    try {
      await api(`/api/chats/${state.chatId}/basic-commands/${state.editingBasicKey}`, {method:'PUT', body:JSON.stringify({trigger,response})});
      $('#basic-command-dialog').close();
      notify('Основная команда обновлена в боте.');
      await loadChat();
    } catch (error) { notify(error.message, true); }
  }

  function updateRulesPreview() {
    const rules = $('#group-rules-text').value.split('\n').map(item => item.trim()).filter(Boolean);
    $('#group-rules-preview').innerHTML = rules.map((rule,index) => `<div class="rule-preview-item"><span>${index + 1}</span><div>${escapeHtml(rule)}</div></div>`).join('') || '<p class="muted">Правила не добавлены.</p>';
  }

  function openRulesDialog() {
    $('#group-rules-text').value = (state.rulesConfig?.rules || []).join('\n');
    updateRulesPreview();
    $('#rules-dialog').showModal();
  }

  async function saveGroupRules() {
    const rules = $('#group-rules-text').value.split('\n').map(item => item.trim()).filter(Boolean);
    try {
      state.rulesConfig = await api(`/api/chats/${state.chatId}/group-rules`, {method:'PUT', body:JSON.stringify({rules})});
      $('#rules-dialog').close();
      notify('Правила группы сохранены и подключены к команде бота.');
      renderProfile();
    } catch (error) { notify(error.message, true); }
  }

  function renderWelcomePhoto() {
    const image = $('#welcome-photo-preview');
    if (state.welcome?.photo_url) {
      image.src = state.welcome.photo_url;
      image.classList.remove('hidden');
    } else {
      image.removeAttribute('src');
      image.classList.add('hidden');
    }
  }

  function openWelcomeDialog() {
    $('#welcome-enabled').checked = Boolean(state.welcome?.enabled);
    $('#welcome-after-captcha').checked = Boolean(state.welcome?.after_captcha);
    $('#welcome-text').value = state.welcome?.text || '';
    $('#welcome-photo').value = '';
    renderWelcomePhoto();
    $('#welcome-dialog').showModal();
  }

  async function saveWelcome() {
    try {
      state.welcome = await api(`/api/chats/${state.chatId}/welcome`, {method:'PUT', body:JSON.stringify({enabled:$('#welcome-enabled').checked,text:$('#welcome-text').value,after_captcha:$('#welcome-after-captcha').checked})});
      $('#welcome-dialog').close();
      notify('Приветственное сообщение подключено к боту.');
      renderProfile();
    } catch (error) { notify(error.message, true); }
  }

  async function uploadWelcomePhoto() {
    const file = $('#welcome-photo').files?.[0];
    if (!file) return notify('Сначала выберите фотографию.', true);
    const form = new FormData();
    form.append('photo', file);
    try {
      const result = await api(`/api/chats/${state.chatId}/welcome/photo`, {method:'POST', body:form});
      state.welcome = {...state.welcome, ...result};
      renderWelcomePhoto();
      renderProfile();
      notify('Фотография загружена на сервер бота.');
    } catch (error) { notify(error.message, true); }
  }

  async function deleteWelcomePhoto() {
    try {
      await api(`/api/chats/${state.chatId}/welcome/photo`, {method:'DELETE'});
      state.welcome = {...state.welcome, photo_name:'', photo_url:null};
      renderWelcomePhoto();
      renderProfile();
      notify('Фотография удалена.');
    } catch (error) { notify(error.message, true); }
  }

  function openCaptchaDialog() {
    const value = state.captcha || {};
    $('#captcha-enabled').checked = Boolean(value.enabled);
    $('#captcha-timeout').value = String(value.timeout_seconds || 60);
    $('#captcha-attempts').value = String(value.attempts || 3);
    $('#captcha-failure').value = value.failure_action || 'kick';
    $('#captcha-image-set').value = value.image_set || 'random';
    $('#captcha-message').value = value.message || '';
    $('#captcha-preview').classList.add('hidden');
    $('#captcha-dialog').showModal();
  }

  async function saveCaptcha() {
    try {
      state.captcha = await api(`/api/chats/${state.chatId}/captcha`, {method:'PUT', body:JSON.stringify({enabled:$('#captcha-enabled').checked,timeout_seconds:Number($('#captcha-timeout').value),attempts:Number($('#captcha-attempts').value),failure_action:$('#captcha-failure').value,image_set:$('#captcha-image-set').value,message:$('#captcha-message').value})});
      state.settings.captcha_enabled = state.captcha.enabled;
      $('#captcha-dialog').close();
      renderProfile();
      renderAutomodValues();
      notify('CAPTCHA сохранена и работает для новых участников.');
    } catch (error) { notify(error.message, true); }
  }

  async function previewCaptcha() {
    try {
      const preview = await api(`/api/chats/${state.chatId}/captcha/preview?image_set=${encodeURIComponent($('#captcha-image-set').value)}`);
      state.captchaPreviewAnswer = preview.answer;
      $('#captcha-preview-image').src = preview.image_url;
      $('#captcha-result').textContent = 'Выберите ответ.';
      $('#captcha-options').innerHTML = preview.options.map(option => `<button type="button" data-captcha-option="${escapeHtml(option)}">${escapeHtml(option)}</button>`).join('');
      $('#captcha-preview').classList.remove('hidden');
    } catch (error) { notify(error.message, true); }
  }

  function handleProfileAction(action) {
    if (action === 'members') setView('members');
    else if (action === 'owner') showOwnerDetails();
    else if (action === 'premium') setView('premium');
    else if (action === 'rules') openRulesDialog();
    else if (action === 'welcome') openWelcomeDialog();
    else if (action === 'captcha') openCaptchaDialog();
  }

  async function loadAdmin() {
    if (!state.user?.is_bot_admin) return;
    try {
      const [overview, logs] = await Promise.all([api('/api/admin/overview'), api('/api/admin/logs?limit=50')]);
      const metrics = [['i-user','Пользователи',overview.users],['i-shield','Группы',overview.chats],['i-premium','Premium',overview.active_premium],['i-ban','Блокировки',overview.blocked],['i-command','Кастомные',overview.custom_commands]];
      $('#admin-metrics').innerHTML = metrics.map(([ic,label,value]) => `<div class="metric">${iconSmall(ic)}<span>${label}</span><strong>${value}</strong></div>`).join('');
      $('#admin-logs').innerHTML = logs.length ? logs.map(row => `<div class="list-row"><b>${escapeHtml(row.action)}</b><p>${escapeHtml(row.entity_type || '')} ${row.entity_id ?? ''} · ${new Date(row.created_at).toLocaleString('ru-RU')}</p></div>`).join('') : '<p class="muted">Действий пока нет.</p>';
      await searchAdminEntities();
    } catch (error) { notify(error.message, true); }
  }

  async function searchAdminEntities() {
    const type = $('#admin-entity-type').value;
    const q = encodeURIComponent($('#admin-search').value.trim());
    try {
      const rows = await api(`/api/admin/entities?entity_type=${type}&q=${q}&limit=100`);
      $('#admin-entities').innerHTML = rows.length ? rows.map(row => adminEntityHtml(type,row)).join('') : '<article class="card muted">Ничего не найдено.</article>';
    } catch (error) { notify(error.message, true); }
  }

  function adminEntityHtml(type, row) {
    const premium = row.premium_permanent ? 'Premium навсегда' : row.premium_until ? `Premium до ${new Date(row.premium_until).toLocaleDateString('ru-RU')}` : 'Без Premium';
    return `<article class="list-card admin-entity" data-entity-type="${type}" data-entity-id="${row.id}"><div>${icon(type === 'user' ? 'i-user' : 'i-shield')}<h3>${escapeHtml(row.title)}${row.username ? ` · @${escapeHtml(row.username)}` : ''}</h3><p>ID: ${row.id} · <span class="${row.premium_until || row.premium_permanent ? 'status-good' : ''}">${premium}</span> · <span class="${row.blocked ? 'status-bad' : 'status-good'}">${row.blocked ? 'заблокирован' : 'доступ открыт'}</span></p>${row.block_reason ? `<p>Причина: ${escapeHtml(row.block_reason)}</p>` : ''}</div><div class="admin-controls"><input class="control admin-days" type="number" min="0" max="3650" value="30" title="Дней"><label class="switch-line"><input class="admin-permanent" type="checkbox"><span>Навсегда</span></label><button class="secondary admin-grant">Выдать Premium</button><button class="secondary admin-revoke">Снять Premium</button><input class="control admin-block-reason" placeholder="Причина блокировки"><input class="control admin-block-duration" placeholder="Срок: пусто = навсегда"><button class="${row.blocked ? 'secondary admin-unblock' : 'danger admin-block'}">${row.blocked ? 'Разблокировать' : 'Заблокировать'}</button></div></article>`;
  }

  async function adminPremium(card, revoke = false) {
    const payload = {entity_type:card.dataset.entityType, entity_id:Number(card.dataset.entityId), days:revoke ? 0 : Number($('.admin-days',card).value), permanent:revoke ? false : $('.admin-permanent',card).checked, plan:'admin', note:'Выдано через глобальную панель'};
    try { await api('/api/admin/premium', {method:'POST', body:JSON.stringify(payload)}); notify(revoke ? 'Premium снят.' : 'Premium выдан.'); await loadAdmin(); }
    catch (error) { notify(error.message, true); }
  }

  async function adminBlock(card, blocked) {
    const durationText = $('.admin-block-duration',card)?.value.trim() || '';
    const payload = {entity_type:card.dataset.entityType, entity_id:Number(card.dataset.entityId), blocked, reason:$('.admin-block-reason',card).value || 'Решение владельца AniGuard'};
    if (blocked && durationText) payload.duration_seconds = parseDurationText(durationText);
    try { await api('/api/admin/block', {method:'POST', body:JSON.stringify(payload)}); notify(blocked ? 'Доступ заблокирован.' : 'Доступ восстановлен.'); await loadAdmin(); }
    catch (error) { notify(error.message, true); }
  }

  async function switchChat(chatId) {
    if (!chatId || Number.isNaN(Number(chatId)) || Number(chatId) === state.chatId) {
      $('#group-dialog')?.close();
      return;
    }
    state.chatId = Number(chatId);
    state.editingCustomId = null;
    state.editingGameId = null;
    $('#chat-select').value = String(state.chatId);
    await loadChat();
    $('#group-dialog')?.close();
    notify('Группа переключена.');
  }

  function bindEvents() {
    document.addEventListener('pointerdown', event => {
      if (event.target.closest('button, .pressable, input[type="checkbox"], select')) haptic('light');
    }, {passive:true});

    document.addEventListener('click', async event => {
      const nav = event.target.closest('.nav[data-view]');
      if (nav) setView(nav.dataset.view);

      const menuLink = event.target.closest('[data-view-target]');
      if (menuLink) {
        $('#menu-dialog').close();
        setView(menuLink.dataset.viewTarget);
      }

      const groupOption = event.target.closest('[data-chat-id]');
      if (groupOption) await switchChat(Number(groupOption.dataset.chatId));

      const action = event.target.closest('[data-action]');
      if (action) openAction(action.dataset.action);

      const buy = event.target.closest('.buy-plan');
      if (buy) buyPlan(buy.dataset.plan);

      const profileAction = event.target.closest('[data-profile-action]');
      if (profileAction) handleProfileAction(profileAction.dataset.profileAction);

      const memberCard = event.target.closest('[data-member-id]');
      if (memberCard && event.target.closest('.member-info')) await showMemberDetails(memberCard.dataset.memberId);
      if (memberCard && event.target.closest('.member-ban')) await memberModeration(memberCard.dataset.memberId, 'ban');
      if (memberCard && event.target.closest('.member-kick')) await memberModeration(memberCard.dataset.memberId, 'kick');

      const basicCard = event.target.closest('[data-basic-key]');
      if (basicCard && event.target.closest('.basic-edit')) openBasicEditor(basicCard.dataset.basicKey);
      if (basicCard && event.target.closest('.basic-run')) {
        const command = state.basicCommands.find(item => item.key === basicCard.dataset.basicKey);
        if (command) openAction(command.action);
      }

      const captchaOption = event.target.closest('[data-captcha-option]');
      if (captchaOption) {
        const correct = captchaOption.dataset.captchaOption === state.captchaPreviewAnswer;
        if (correct) captchaOption.classList.add('correct');
        $('#captcha-result').textContent = correct ? 'Верный ответ. Пользователь получит доступ к беседе.' : 'Неверный ответ. Попытка будет уменьшена.';
        tg?.HapticFeedback?.notificationOccurred(correct ? 'success' : 'error');
      }

      const cardReport = event.target.closest('[data-report-id]');
      const decision = event.target.closest('.report-decision');
      if (decision && cardReport) {
        try {
          await api(`/api/chats/${state.chatId}/reports/${cardReport.dataset.reportId}/decision`, {
            method:'POST',
            body:JSON.stringify({decision:decision.dataset.decision, duration_seconds:604800, reason:'Решение из Mini App'}),
          });
          notify('Решение выполнено.');
          await loadChat();
        } catch(error) { notify(error.message,true); }
      }

      const customCard = event.target.closest('[data-custom-id]');
      if (customCard && event.target.closest('.custom-edit')) editCustom(customCard.dataset.customId);
      if (customCard && event.target.closest('.custom-delete')) {
        try { await api(`/api/chats/${state.chatId}/custom-commands/${customCard.dataset.customId}`, {method:'DELETE'}); notify('Команда удалена.'); await loadChat(); }
        catch(error){ notify(error.message,true); }
      }
      if (customCard && event.target.closest('.custom-toggle')) {
        const button=event.target.closest('.custom-toggle');
        try { await api(`/api/chats/${state.chatId}/custom-commands/${customCard.dataset.customId}`, {method:'PATCH', body:JSON.stringify({enabled:button.dataset.enabled !== 'true'})}); notify('Состояние изменено.'); await loadChat(); }
        catch(error){ notify(error.message,true); }
      }

      const gameCard = event.target.closest('[data-game-id]');
      if (gameCard && event.target.closest('.game-edit')) editGame(gameCard.dataset.gameId);
      if (gameCard && event.target.closest('.game-delete')) {
        try { await api(`/api/chats/${state.chatId}/game-commands/${gameCard.dataset.gameId}`, {method:'DELETE'}); notify('Игровая команда удалена.'); await loadChat(); }
        catch(error){ notify(error.message,true); }
      }
      if (gameCard && event.target.closest('.game-toggle')) {
        const button=event.target.closest('.game-toggle');
        try { await api(`/api/chats/${state.chatId}/game-commands/${gameCard.dataset.gameId}`, {method:'PATCH', body:JSON.stringify({enabled:button.dataset.enabled !== 'true'})}); notify('Состояние изменено.'); await loadChat(); }
        catch(error){ notify(error.message,true); }
      }

      const rpCard = event.target.closest('[data-rp-id]');
      if (rpCard && event.target.closest('.rp-delete')) {
        try { await api(`/api/chats/${state.chatId}/rp/${rpCard.dataset.rpId}`, {method:'DELETE'}); notify('RP-команда удалена.'); await loadChat(); }
        catch(error){ notify(error.message,true); }
      }
      if (rpCard && event.target.closest('.rp-toggle')) {
        const button=event.target.closest('.rp-toggle');
        try { await api(`/api/chats/${state.chatId}/rp/${rpCard.dataset.rpId}`, {method:'PATCH', body:JSON.stringify({enabled:button.dataset.enabled !== 'true'})}); notify('Состояние изменено.'); await loadChat(); }
        catch(error){ notify(error.message,true); }
      }

      const ruleCard = event.target.closest('[data-rule-id]');
      if (ruleCard && event.target.closest('.rule-delete')) {
        try { await api(`/api/chats/${state.chatId}/rules/${ruleCard.dataset.ruleId}`, {method:'DELETE'}); notify('Правило удалено.'); await loadChat(); }
        catch(error){ notify(error.message,true); }
      }
      if (ruleCard && event.target.closest('.rule-toggle')) {
        const button=event.target.closest('.rule-toggle');
        try { await api(`/api/chats/${state.chatId}/rules/${ruleCard.dataset.ruleId}`, {method:'PATCH', body:JSON.stringify({enabled:button.dataset.enabled !== 'true'})}); notify('Состояние изменено.'); await loadChat(); }
        catch(error){ notify(error.message,true); }
      }

      const adminCard = event.target.closest('[data-entity-id]');
      if (adminCard && event.target.closest('.admin-grant')) adminPremium(adminCard,false);
      if (adminCard && event.target.closest('.admin-revoke')) adminPremium(adminCard,true);
      if (adminCard && event.target.closest('.admin-block')) adminBlock(adminCard,true);
      if (adminCard && event.target.closest('.admin-unblock')) adminBlock(adminCard,false);
    });

    document.addEventListener('change', async event => {
      const roleSelect = event.target.closest('.member-role');
      if (roleSelect && !roleSelect.disabled) {
        const card = roleSelect.closest('[data-member-id]');
        try {
          await api(`/api/chats/${state.chatId}/members/${card.dataset.memberId}/role`, {method:'PATCH', body:JSON.stringify({role:roleSelect.value})});
          notify('Роль участника обновлена.');
          await loadChat();
        } catch (error) { notify(error.message, true); }
        return;
      }

      const automodInput = event.target.closest('[data-automod-key]');
      if (automodInput) {
        if (automodInput.dataset.premium === 'true' && automodInput.checked && !premiumAvailable()) {
          automodInput.checked = false;
          openPremiumDialog(`Функция «${automodInput.closest('.automod-row').querySelector('.automod-title').textContent}» доступна с Premium.`);
          return;
        }
        await saveSettingKey(automodInput.dataset.automodKey, automodInput.checked, automodInput);
        return;
      }

      const quickInput = event.target.closest('[data-quick-setting]');
      if (quickInput) {
        await saveSettingKey(quickInput.dataset.quickSetting, quickInput.checked, quickInput);
        return;
      }

      const premiumSetting = event.target.closest('[data-premium-setting="true"]');
      if (premiumSetting && premiumSetting.type === 'checkbox' && premiumSetting.checked && !premiumAvailable()) {
        premiumSetting.checked = false;
        openPremiumDialog('Эта настройка доступна только с AniGuard Premium.');
      }
    });

    $('#chat-select').addEventListener('change', event => switchChat(Number(event.target.value)));
    $('#group-switch-button').addEventListener('click', () => { renderChatSelect(); $('#group-dialog').showModal(); });
    $('#close-group-dialog').addEventListener('click', () => $('#group-dialog').close());
    $('#header-menu-button').addEventListener('click', () => $('#menu-dialog').showModal());
    $('#close-menu-dialog').addEventListener('click', () => $('#menu-dialog').close());
    $('#header-search-button').addEventListener('click', () => { $('#global-search').focus(); haptic('medium'); });

    $('#premium-dialog-close').addEventListener('click', () => $('#premium-dialog').close());
    $('#premium-dialog-open').addEventListener('click', () => { $('#premium-dialog').close(); setView('premium'); });

    $('#profile-open-link').addEventListener('click', () => {
      const profile = state.profile;
      const url = profile?.username ? `https://t.me/${profile.username}` : profile?.invite_link;
      if (!url) return notify('Ссылка группы недоступна.', true);
      if (url.includes('t.me/')) tg?.openTelegramLink ? tg.openTelegramLink(url) : window.open(url, '_blank', 'noopener');
      else tg?.openLink ? tg.openLink(url) : window.open(url, '_blank', 'noopener');
    });

    $('#close-member-dialog').addEventListener('click', () => $('#member-dialog').close());
    $('#close-owner-dialog').addEventListener('click', () => $('#owner-dialog').close());
    $('#close-basic-command').addEventListener('click', () => $('#basic-command-dialog').close());
    $('#save-basic-command').addEventListener('click', saveBasicCommand);
    $('#close-rules-dialog').addEventListener('click', () => $('#rules-dialog').close());
    $('#group-rules-text').addEventListener('input', updateRulesPreview);
    $('#save-group-rules').addEventListener('click', saveGroupRules);
    $('#close-welcome-dialog').addEventListener('click', () => $('#welcome-dialog').close());
    $('#save-welcome').addEventListener('click', saveWelcome);
    $('#upload-welcome-photo').addEventListener('click', uploadWelcomePhoto);
    $('#delete-welcome-photo').addEventListener('click', deleteWelcomePhoto);
    $('#close-captcha-dialog').addEventListener('click', () => $('#captcha-dialog').close());
    $('#save-captcha').addEventListener('click', saveCaptcha);
    $('#preview-captcha').addEventListener('click', previewCaptcha);
    $('#members-search').addEventListener('input', event => renderMembers(event.target.value));

    $('#close-action').addEventListener('click', () => $('#action-dialog').close());
    $('#execute-action').addEventListener('click', executeAction);
    $('#create-custom').addEventListener('click', createCustom);
    $('#create-game').addEventListener('click', createGame);
    $('#create-rp').addEventListener('click', createRp);
    $('#create-rule').addEventListener('click', createRule);
    $('#save-settings').addEventListener('click', saveSettings);
    $('#admin-search-button').addEventListener('click', searchAdminEntities);
    $('#admin-entity-type').addEventListener('change', searchAdminEntities);

    $('#command-search').addEventListener('input', event => {
      const q=event.target.value.toLowerCase().trim();
      $$('#regular-actions .action').forEach(node => node.classList.toggle('hidden', !node.dataset.search.includes(q)));
    });
    $('#settings-search').addEventListener('input', event => {
      const q=event.target.value.toLowerCase().trim();
      $$('.setting-group').forEach(node => {
        const match=node.dataset.search.includes(q)||node.textContent.toLowerCase().includes(q);
        node.classList.toggle('hidden',!match);
        if(q&&match) node.open=true;
      });
    });
    $('#global-search').addEventListener('input', event => {
      const q = event.target.value.toLowerCase().trim();
      const active = $(`.view[data-panel="${state.currentView}"]`);
      if (!active) return;
      $$('.telegram-row, .action, .automod-row, .list-card, .setting-group', active).forEach(node => {
        node.classList.toggle('hidden', Boolean(q) && !node.textContent.toLowerCase().includes(q));
      });
    });
  }


  init();
})();
