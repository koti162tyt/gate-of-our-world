'use strict';

const state = {
  hasToken: false,
  channelId: '',
  webhooks: [],
  activeWebhookId: '',
  characters: [],
  useUsername: true,
  current: '',
};

const $ = (id) => document.getElementById(id);

/* ------------------------------------------------------------------- api */

async function api(path, body) {
  const res = await fetch(path, {
    method: 'POST',
    headers: { 'content-type': 'application/json' },
    body: JSON.stringify(body || {}),
  });
  const data = await res.json().catch(() => ({ ok: false, error: `HTTP ${res.status}` }));
  if (!res.ok || !data.ok) {
    throw new Error(data.error || `HTTP ${res.status}`);
  }
  return data;
}

function apply(next) {
  Object.assign(state, {
    hasToken: next.hasToken,
    channelId: next.channelId,
    webhooks: next.webhooks || [],
    activeWebhookId: next.activeWebhookId || '',
    characters: next.characters || [],
    useUsername: next.useUsername,
  });
  if (!state.current && next.lastCharacter) state.current = next.lastCharacter;
  if (!state.current && state.characters.length) state.current = state.characters[0].name;
  render();
}

function toast(message, kind = '') {
  const el = $('toast');
  el.textContent = message;
  el.className = `toast show ${kind}`;
  clearTimeout(toast.timer);
  toast.timer = setTimeout(() => {
    el.className = 'toast';
  }, 3200);
}

/* ---------------------------------------------------------------- render */

function activeHook() {
  return state.webhooks.find((w) => w.id === state.activeWebhookId) || null;
}

function render() {
  // статус
  const hook = activeHook();
  $('statusDot').className = `dot ${hook ? 'on' : ''}`;
  const badge = $('hookBadge');
  badge.className = `hookBadge ${hook ? 'on' : ''}`;
  badge.textContent = hook ? `вебхук: ${hook.name}` : 'вебхук не выбран';
  badge.title = hook ? hook.url : '';

  $('whoLabel').textContent = state.current || '—';
  $('useName').checked = state.useUsername;
  $('sendBtn').disabled = !hook || !state.characters.length;

  renderCharacters();
  renderCharRows();
  renderHookRows();
  renderPreview();
}

function avatarNode(character) {
  const wrap = document.createElement('span');
  wrap.className = 'avatar';
  const src = character.avatar || '';
  if (src.startsWith('https://') || src.startsWith('/avatars/')) {
    const img = document.createElement('img');
    img.src = src;
    img.alt = '';
    img.onerror = () => {
      img.remove();
      wrap.textContent = (character.name || '?').slice(0, 1).toUpperCase();
    };
    wrap.appendChild(img);
  } else {
    wrap.textContent = (character.name || '?').slice(0, 1).toUpperCase();
  }
  return wrap;
}

/* -------------------------------------------------------------- картинки */

function askFile() {
  return new Promise((resolve) => {
    const input = document.createElement('input');
    input.type = 'file';
    input.accept = 'image/*';
    input.onchange = () => resolve(input.files[0] || null);
    input.click();
  });
}

function fileToDataUrl(file, size = 256) {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onerror = () => reject(new Error('Не удалось прочитать файл'));
    reader.onload = () => {
      const img = new Image();
      img.onerror = () => reject(new Error('Это не картинка'));
      img.onload = () => {
        const side = Math.min(img.width, img.height);
        const canvas = document.createElement('canvas');
        canvas.width = size;
        canvas.height = size;
        const ctx = canvas.getContext('2d');
        ctx.drawImage(
          img,
          (img.width - side) / 2,
          (img.height - side) / 2,
          side,
          side,
          0,
          0,
          size,
          size
        );
        resolve(canvas.toDataURL('image/png'));
      };
      img.src = reader.result;
    };
    reader.readAsDataURL(file);
  });
}

async function uploadCharacterPhoto(name) {
  try {
    const file = await askFile();
    if (!file) return;
    const dataUrl = await fileToDataUrl(file);
    const next = await api('/api/characters/avatar', { name, dataUrl });
    apply(next);
    toast(next.warning || `Фото обновлено: ${name}`, next.warning ? '' : 'ok');
  } catch (err) {
    toast(err.message, 'err');
  }
}

async function uploadHookPhoto(hookId) {
  try {
    const file = await askFile();
    if (!file) return;
    const dataUrl = await fileToDataUrl(file);
    apply(await api('/api/webhook/avatar', { id: hookId, dataUrl }));
    toast('Аватар вебхука обновлён', 'ok');
  } catch (err) {
    toast(err.message, 'err');
  }
}

async function checkAvatarLink(url) {
  if (!url) return toast('Сначала вставь ссылку', 'err');
  try {
    const res = await fetch(`/api/avatar/check?url=${encodeURIComponent(url)}`);
    const data = await res.json();
    if (data.isImage) return toast(`Картинка ок (${data.contentType})`, 'ok');
    toast(`${data.hint || 'Не картинка'}${data.status ? ` [${data.status}]` : ''}`, 'err');
  } catch (err) {
    toast(err.message, 'err');
  }
}

function renderCharacters() {
  const list = $('charList');
  list.innerHTML = '';

  if (!state.characters.length) {
    list.innerHTML = '<p class="muted">Пока никого. Добавь персонажа на вкладке «Персонажи».</p>';
    return;
  }

  state.characters.forEach((character) => {
    const btn = document.createElement('button');
    btn.className = `char ${character.name === state.current ? 'active' : ''}`;
    btn.type = 'button';
    btn.appendChild(avatarNode(character));
    const label = document.createElement('span');
    label.textContent = character.name;
    btn.appendChild(label);
    btn.onclick = () => {
      state.current = character.name;
      render();
      $('messageBox').focus();
    };
    list.appendChild(btn);
  });
}

function renderCharRows() {
  const rows = $('charsRows');
  rows.innerHTML = '';

  state.characters.forEach((character) => {
    const row = document.createElement('div');
    row.className = 'row';

    const name = document.createElement('input');
    name.type = 'text';
    name.value = character.name;
    name.className = 'grow';
    name.maxLength = 80;

    const avatar = document.createElement('input');
    avatar.type = 'url';
    avatar.value = character.avatar || '';
    avatar.placeholder = 'аватар (https://…)';
    avatar.className = 'grow';

    const check = document.createElement('button');
    check.className = 'small';
    check.textContent = 'Проверить';
    check.title = 'Открывается ли ссылка и картинка ли это';
    check.onclick = () => checkAvatarLink(avatar.value.trim());

    const photo = document.createElement('button');
    photo.className = 'small';
    photo.textContent = 'Фото';
    photo.title = 'Загрузить картинку файлом (нужен токен бота)';
    photo.onclick = () => uploadCharacterPhoto(character.name);

    const save = document.createElement('button');
    save.className = 'small';
    save.textContent = 'Сохранить';
    save.onclick = async () => {
      try {
        apply(
          await api('/api/characters', {
            action: 'update',
            oldName: character.name,
            character: { name: name.value.trim(), avatar: avatar.value.trim() },
          })
        );
        if (state.current === character.name) state.current = name.value.trim();
        toast('Персонаж обновлён', 'ok');
        render();
      } catch (err) {
        toast(err.message, 'err');
      }
    };

    const del = document.createElement('button');
    del.className = 'small danger';
    del.textContent = 'Удалить';
    del.onclick = async () => {
      if (!confirm(`Удалить персонажа «${character.name}»?`)) return;
      try {
        apply(await api('/api/characters', { action: 'delete', character: { name: character.name } }));
        toast('Удалён', 'ok');
      } catch (err) {
        toast(err.message, 'err');
      }
    };

    row.append(name, avatar, save, photo, check, del);
    rows.appendChild(row);
  });
}

function renderHookRows() {
  const rows = $('hookRows');
  rows.innerHTML = '';

  if (!state.webhooks.length) {
    rows.innerHTML = '<p class="muted">Вебхуков нет. Создай через бота или вставь ссылку.</p>';
  }

  state.webhooks.forEach((hook) => {
    const row = document.createElement('div');
    row.className = `row ${hook.id === state.activeWebhookId ? 'active' : ''}`;

    const name = document.createElement('input');
    name.type = 'text';
    name.value = hook.name;
    name.className = 'grow';
    name.maxLength = 80;
    name.title = 'Имя вебхука в Discord';

    const rename = document.createElement('button');
    rename.className = 'small';
    rename.textContent = 'Переименовать';
    rename.onclick = async () => {
      try {
        apply(await api('/api/webhook/rename', { id: hook.id, name: name.value.trim() }));
        toast('Имя вебхука изменено', 'ok');
      } catch (err) {
        toast(err.message, 'err');
      }
    };

    const pick = document.createElement('button');
    pick.className = 'small';
    pick.textContent = hook.id === state.activeWebhookId ? 'активен' : 'сделать активным';
    pick.disabled = hook.id === state.activeWebhookId;
    pick.onclick = async () => {
      try {
        apply(await api('/api/settings', { activeWebhookId: hook.id }));
        toast('Активный вебхук выбран', 'ok');
      } catch (err) {
        toast(err.message, 'err');
      }
    };

    const del = document.createElement('button');
    del.className = 'small danger';
    del.textContent = 'Удалить';
    del.onclick = async () => {
      if (!confirm('Удалить вебхук? Если есть токен бота — он удалится и из Discord.')) return;
      try {
        apply(await api('/api/webhook/delete', { id: hook.id }));
        toast('Вебхук удалён', 'ok');
      } catch (err) {
        toast(err.message, 'err');
      }
    };

    const photo = document.createElement('button');
    photo.className = 'small';
    photo.textContent = 'Фото';
    photo.title = 'Аватар этого вебхука (нужен токен бота)';
    photo.onclick = () => uploadHookPhoto(hook.id);

    const sub = document.createElement('div');
    sub.className = 'sub';
    sub.textContent = hook.id;

    row.append(name, rename, pick, photo, del, sub);
    rows.appendChild(row);
  });
}

/* --------------------------------------------------------------- вкладки */

$('tabs').addEventListener('click', (event) => {
  const tab = event.target.closest('.tab');
  if (!tab) return;
  document.querySelectorAll('.tab').forEach((t) => t.classList.toggle('active', t === tab));
  document.querySelectorAll('.pane').forEach((p) => p.classList.remove('active'));
  $(`pane-${tab.dataset.tab}`).classList.add('active');
});

/* ---------------------------------------------------------- предпросмотр */

function escapeHtml(value) {
  return String(value).replace(/[&<>"']/g, (ch) => (
    { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[ch]
  ));
}

function renderMarkdown(raw) {
  let text = escapeHtml(raw);

  text = text.replace(/```([\s\S]*?)```/g, (_, code) =>
    `<pre class="dcode">${code.replace(/^\n/, '')}</pre>`
  );
  text = text.replace(/`([^`\n]+)`/g, '<code class="dcode-inline">$1</code>');
  text = text.replace(/\*\*([^*\n]+)\*\*/g, '<strong>$1</strong>');
  text = text.replace(/__([^_\n]+)__/g, '<strong>$1</strong>');
  text = text.replace(/(^|[^*])\*([^*\n]+)\*/g, '$1<em>$2</em>');
  text = text.replace(/(^|[^_])_([^_\n]+)_/g, '$1<em>$2</em>');
  text = text.replace(/~~([^~\n]+)~~/g, '<s>$1</s>');
  text = text.replace(/\|\|([^|\n]+)\|\|/g, '<span class="dspoiler">$1</span>');
  text = text.replace(/^&gt; ?(.*)$/gm, '<span class="dquote">$1</span>');
  text = text.replace(
    /\[([^\]]+)\]\((https?:\/\/[^\s)]+)\)/g,
    '<a href="$2" target="_blank" rel="noreferrer">$1</a>'
  );
  text = text.replace(
    /(^|[\s(])(https?:\/\/[^\s<)]+)/g,
    '$1<a href="$2" target="_blank" rel="noreferrer">$2</a>'
  );

  return text;
}

function nameColor(name) {
  let hash = 0;
  for (const ch of String(name)) hash = (hash * 31 + ch.codePointAt(0)) % 360;
  return `hsl(${hash}, 65%, 72%)`;
}

function renderPreview() {
  const preview = $('preview');
  const text = box.value;
  const hook = activeHook();
  const speaker = state.useUsername
    ? state.current || (hook && hook.name) || ''
    : (hook && hook.name) || '';

  if (!text.trim() && !speaker) {
    preview.innerHTML = '<div class="empty">Пусто. Начни печатать — увидишь, как это придёт в канал.</div>';
    return;
  }

  const character = state.characters.find((c) => c.name === state.current);
  const rawAvatar = (character && character.avatar) || '';
  const avatarUrl = rawAvatar.startsWith('https://') || rawAvatar.startsWith('/avatars/')
    ? rawAvatar
    : '';
  const initial = (speaker || '?').trim().slice(0, 1).toUpperCase();

  const now = new Date();
  const time = `сегодня в ${String(now.getHours()).padStart(2, '0')}:${String(now.getMinutes()).padStart(2, '0')}`;

  const avatar = avatarUrl
    ? `<span class="davatar"><img src="${escapeHtml(avatarUrl)}" alt="" /></span>`
    : `<span class="davatar" style="background:${nameColor(speaker)}">${escapeHtml(initial)}</span>`;

  const body = text.trim()
    ? `<div class="dtext">${renderMarkdown(text)}</div>`
    : '<div class="dtext" style="color:#7d8288">…</div>';

  preview.innerHTML = `
    <div class="dmsg">
      ${avatar}
      <div class="dbody">
        <div class="dhead">
          <span class="dname" style="color:${nameColor(speaker)}">${escapeHtml(speaker || 'без имени')}</span>
          <span class="dtag">APP</span>
          <span class="dtime">${time}</span>
        </div>
        ${body}
      </div>
    </div>`;
}

/* --------------------------------------------------------------- отправка */

const box = $('messageBox');

function updateCounter() {
  const len = box.value.length;
  $('counter').textContent = `${len} / 2000`;
  $('counter').className = `counter ${len > 2000 ? 'over' : ''}`;
  $('sendBtn').disabled = !activeHook() || !state.current || len === 0 || len > 2000;
  renderPreview();
}

box.addEventListener('input', updateCounter);

box.addEventListener('keydown', (event) => {
  if (event.key === 'Enter' && !event.shiftKey && !event.isComposing) {
    event.preventDefault();
    send();
  }
});

$('useName').addEventListener('change', async (event) => {
  try {
    apply(await api('/api/settings', { useUsername: event.target.checked }));
  } catch (err) {
    toast(err.message, 'err');
  }
});

$('sendBtn').addEventListener('click', send);

async function send() {
  const text = box.value.trim();
  if (!text) return;
  const button = $('sendBtn');
  button.disabled = true;
  button.textContent = 'Отправляю…';
  try {
    await api('/api/send', { text, character: state.current });
    box.value = '';
    updateCounter();
    toast(`Отправлено как «${state.current}»`, 'ok');
  } catch (err) {
    toast(err.message, 'err');
  } finally {
    button.textContent = 'Отправить';
    updateCounter();
  }
}

/* ---------------------------------------------------------------- формы */

$('charForm').addEventListener('submit', async (event) => {
  event.preventDefault();
  const name = $('charName').value.trim();
  const avatar = $('charAvatar').value.trim();
  if (!name) return;
  try {
    apply(await api('/api/characters', { action: 'add', character: { name, avatar } }));
    $('charName').value = '';
    $('charAvatar').value = '';
    state.current = name;
    toast('Персонаж добавлен', 'ok');
    render();
  } catch (err) {
    toast(err.message, 'err');
  }
});

$('createForm').addEventListener('submit', async (event) => {
  event.preventDefault();
  const token = $('token').value.trim();
  const channelId = $('channelId').value.trim();
  const name = $('newHookName').value.trim() || 'Голос';

  try {
    if (token || channelId) {
      const patch = {};
      if (token) patch.token = token;
      if (channelId) patch.channelId = channelId;
      apply(await api('/api/settings', patch));
    }
    const next = await api('/api/webhook/create', { name, channelId });
    apply(next);
    if (!$('rememberToken').checked) {
      apply(await api('/api/settings', { token: '' }));
    }
    $('token').value = '';
    toast(`Вебхук «${name}» создан`, 'ok');
  } catch (err) {
    toast(err.message, 'err');
  }
});

$('addForm').addEventListener('submit', async (event) => {
  event.preventDefault();
  const url = $('hookUrl').value.trim();
  if (!url) return;
  try {
    apply(await api('/api/webhook/add', { url }));
    $('hookUrl').value = '';
    toast('Вебхук добавлен', 'ok');
  } catch (err) {
    toast(err.message, 'err');
  }
});

$('quickAddChar').addEventListener('click', () => {
  document.querySelector('.tab[data-tab="chars"]').click();
  $('charName').focus();
});

/* ------------------------------------------------------------------ старт */

(async function init() {
  try {
    const res = await fetch('/api/state');
    apply(await res.json());
  } catch (err) {
    toast(`Сервер не отвечает: ${err.message}`, 'err');
  }
  updateCounter();
  box.focus();
})();