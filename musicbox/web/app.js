/* MusicBox UI — dependency-free, no external requests. */
(() => {
  "use strict";

  const $ = (sel, root = document) => root.querySelector(sel);
  const $$ = (sel, root = document) => Array.from(root.querySelectorAll(sel));

  const state = {
    providers: [],
    filter: "all",
    quality: localStorage.getItem("mb.quality") || "auto",
    queue: [],
    index: -1,
    mode: localStorage.getItem("mb.mode") || "loop", // loop | one | shuffle
    lyrics: [],
    lyricIndex: -1,
    currentPlaylist: null,
    playing: false,
  };

  const audio = $("#audio");

  /* ---------------------------------------------------------------- api */
  async function api(path, options = {}) {
    const res = await fetch(path, {
      headers: { "Content-Type": "application/json" },
      ...options,
    });
    let body = null;
    try { body = await res.json(); } catch (_) { body = null; }
    if (!res.ok) {
      const detail = (body && (body.detail || body.message)) || `HTTP ${res.status}`;
      throw new Error(detail);
    }
    return body;
  }

  function toast(msg, ms = 2400) {
    const el = $("#toast");
    el.textContent = msg;
    el.classList.add("show");
    clearTimeout(toast._t);
    toast._t = setTimeout(() => el.classList.remove("show"), ms);
  }

  const fmtTime = (s) => {
    if (!Number.isFinite(s) || s < 0) return "0:00";
    const m = Math.floor(s / 60);
    const sec = Math.floor(s % 60);
    return `${m}:${String(sec).padStart(2, "0")}`;
  };

  const esc = (s) =>
    String(s == null ? "" : s).replace(/[&<>"']/g, (c) => (
      { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]
    ));

  /* Colors come from provider metadata, but validate before putting them in a
     style attribute: esc() escapes HTML, not CSS. */
  const safeColor = (value) =>
    /^#[0-9a-f]{6}$/i.test(String(value || "")) ? value : "#8b98a9";

  /* Cover art is a provider-supplied URL; only https images may reach CSS
     url(), otherwise a crafted value could smuggle in extra CSS or beacon. */
  const safeCover = (value) => {
    if (!value) return "";
    try {
      const url = new URL(value, location.origin);
      return url.protocol === "https:" ? url.href : "";
    } catch (_) {
      return "";
    }
  };

  /* ------------------------------------------------------------ routing */
  function showView(name) {
    $$(".view").forEach((v) => v.classList.add("hidden"));
    $(`#view-${name}`)?.classList.remove("hidden");
    $$(".nav-btn").forEach((b) => b.classList.toggle("active", b.dataset.view === name));
    if (name === "playlists") loadPlaylists();
    if (name === "history") loadHistory();
    if (name === "accounts") loadAccounts();
  }

  $$(".nav-btn").forEach((btn) =>
    btn.addEventListener("click", () => showView(btn.dataset.view))
  );

  /* ---------------------------------------------------------- providers */
  async function loadProviders() {
    try {
      const data = await api("/api/providers");
      state.providers = data.providers || [];
      renderProviderFilter();
      renderAccounts();
      const logged = state.providers.filter((p) => p.loggedIn).length;
      $("#versionLabel").textContent = `v1.0.2 · ${logged} 个账号已绑定`;
    } catch (err) {
      toast(`平台列表加载失败：${err.message}`);
    }
  }

  function renderProviderFilter() {
    const box = $("#providerFilter");
    box.innerHTML = '<div class="label">来源</div>';
    const all = document.createElement("button");
    all.className = "chip" + (state.filter === "all" ? " on" : "");
    all.innerHTML = `<span class="dot" style="background:#8b98a9"></span>全部平台`;
    all.addEventListener("click", () => { state.filter = "all"; renderProviderFilter(); });
    box.appendChild(all);

    state.providers.forEach((p) => {
      const btn = document.createElement("button");
      btn.className = "chip" + (state.filter === p.id ? " on" : "");
      btn.innerHTML = `<span class="dot" style="background:${safeColor(p.color)}"></span>${esc(p.name)}
        <span class="state">${p.loggedIn ? "✓" : ""}</span>`;
      btn.title = p.loggedIn ? `已绑定（${p.cookieCount} 个 Cookie）` : "未绑定（仅可播放免费曲目）";
      btn.addEventListener("click", () => { state.filter = p.id; renderProviderFilter(); });
      box.appendChild(btn);
    });
  }

  const providerById = (id) => state.providers.find((p) => p.id === id);
  const providerColor = (id) => (providerById(id) || {}).color || "#8b98a9";
  const providerName = (id) => (providerById(id) || {}).name || id;

  /* -------------------------------------------------------------- search */
  $("#searchForm").addEventListener("submit", async (e) => {
    e.preventDefault();
    const q = $("#searchInput").value.trim();
    if (!q) return;
    const list = $("#searchResults");
    const errors = $("#searchErrors");
    list.innerHTML = '<div class="empty">搜索中…</div>';
    errors.innerHTML = "";
    $("#searchMeta").textContent = `正在搜索「${q}」`;
    try {
      const data = await api(
        `/api/search?q=${encodeURIComponent(q)}&provider=${encodeURIComponent(state.filter)}&limit=30`
      );
      state.queue = data.tracks;
      state.index = -1;
      renderTracks(list, data.tracks, { context: "search" });
      $("#searchMeta").textContent = `「${q}」共 ${data.count} 首`;
      (data.errors || []).forEach((err) => {
        const div = document.createElement("div");
        div.textContent = `${providerName(err.provider)}：${err.error}`;
        errors.appendChild(div);
      });
      if (!data.tracks.length) {
        list.innerHTML = '<div class="empty">没有找到结果，换个关键词试试</div>';
      }
    } catch (err) {
      list.innerHTML = '<div class="empty">搜索失败</div>';
      errors.innerHTML = `<div>${esc(err.message)}</div>`;
    }
  });

  $("#qualitySelect").value = state.quality;
  $("#qualitySelect").addEventListener("change", (e) => {
    state.quality = e.target.value;
    localStorage.setItem("mb.quality", state.quality);
    toast(`音质：${e.target.selectedOptions[0].textContent}`);
  });

  /* --------------------------------------------------------- track list */
  function renderTracks(container, tracks, opts = {}) {
    container.innerHTML = "";
    if (!tracks.length) {
      container.innerHTML = '<div class="empty">这里还没有歌曲</div>';
      return;
    }
    tracks.forEach((track, i) => {
      const row = document.createElement("div");
      row.className = "track";
      row.dataset.key = `${track.provider}:${track.id}`;
      row.innerHTML = `
        <div class="idx">${i + 1}</div>
        <div class="info">
          <div class="name">${esc(track.title || "未知曲目")}</div>
          <div class="sub">${esc(track.artistText || (track.artists || []).join(" / "))}
            ${track.album ? " · " + esc(track.album) : ""}
            ${track.duration ? " · " + fmtTime(track.duration) : ""}</div>
        </div>
        <span class="badge" style="border-color:${safeColor(providerColor(track.provider))}55;color:${safeColor(providerColor(track.provider))}">${esc(providerName(track.provider))}</span>
        <div class="acts">
          <button data-act="play" title="播放">▶</button>
          <button data-act="next" title="下一首播放">⏭</button>
          <button data-act="add" title="加入歌单">＋</button>
        </div>`;

      row.addEventListener("dblclick", () => playAt(tracks, i, opts.context));
      row.addEventListener("click", async (e) => {
        const act = e.target.dataset.act;
        if (!act) return;
        if (act === "play") playAt(tracks, i, opts.context);
        if (act === "next") {
          state.queue.splice(state.index + 1, 0, track);
          toast("已加入下一首");
        }
        if (act === "add") await addToPlaylist(track);
      });
      container.appendChild(row);
    });
    highlightPlaying();
  }

  function highlightPlaying() {
    const cur = state.queue[state.index];
    const key = cur ? `${cur.provider}:${cur.id}` : null;
    $$(".track").forEach((row) => {
      row.classList.toggle("playing", !!key && row.dataset.key === key);
    });
  }

  async function addToPlaylist(track) {
    try {
      const { playlists } = await api("/api/playlists");
      if (!playlists.length) {
        const name = prompt("还没有歌单，先建一个：", "我的收藏");
        if (!name) return;
        const created = await api("/api/playlists", {
          method: "POST",
          body: JSON.stringify({ name }),
        });
        playlists.push({ id: created.id, name });
        const res = await api(`/api/playlists/${created.id}/items`, {
          method: "POST",
          body: JSON.stringify(track),
        });
        toast(res.duplicate ? "已在歌单中" : `已加入「${name}」`);
        return;
      }
      const names = playlists.map((p, i) => `${i + 1}. ${p.name}`).join("\n");
      const pick = prompt(`加入哪个歌单？输入序号：\n${names}`, "1");
      if (!pick) return;
      const target = playlists[Number(pick) - 1];
      if (!target) return toast("序号无效");
      const res = await api(`/api/playlists/${target.id}/items`, {
        method: "POST",
        body: JSON.stringify(track),
      });
      toast(res.duplicate ? "已在歌单中" : `已加入「${target.name}」`);
    } catch (err) {
      toast(`加入失败：${err.message}`);
    }
  }

  /* ------------------------------------------------------------ playback */
  async function playAt(tracks, index, context) {
    state.queue = tracks.slice();
    state.index = index;
    await playCurrent();
    if (context === "search") {
      // keep the search list as the visible queue
    }
  }

  async function playCurrent() {
    const track = state.queue[state.index];
    if (!track) return;
    setNowPlaying(track);
    highlightPlaying();
    await attachSource(track);
    try {
      await audio.play();
      state.playing = true;
      updatePlayButton();
    } catch (err) {
      state.playing = false;
      updatePlayButton();
      toast(`播放失败：${err.message || "浏览器拒绝了播放"}`, 3600);
    }
    loadLyrics(track);
    api("/api/history", { method: "POST", body: JSON.stringify(track) }).catch(() => {});
  }

  /**
   * Ask the server to resolve this track and hand back a /api/audio URL.
   * Going through /api/stream (instead of building the URL here) is what lets
   * the server receive the high-quality candidate hashes, without which every
   * paid account silently downloads 128K.
   */
  async function attachSource(track) {
    const params = new URLSearchParams({ quality: state.quality });
    if (track.duration) params.set("duration", String(track.duration));
    const extra = track.extra || {};
    if (extra.hash320) params.set("hash320", extra.hash320);
    if (extra.hashFlac) params.set("hashFlac", extra.hashFlac);
    if (extra.hasLossless) params.set("hasLossless", "true");
    try {
      const info = await api(
        `/api/stream/${encodeURIComponent(track.provider)}/${encodeURIComponent(track.id)}?${params}`
      );
      audio.src = info.src;
      return info;
    } catch (err) {
      // Let the audio error path handle it: it knows how to find another source.
      toast(err.message, 4000);
      throw err;
    }
  }

  function setNowPlaying(track) {
    $("#nowTitle").textContent = track.title || "未知曲目";
    $("#nowArtist").textContent =
      (track.artistText || (track.artists || []).join(" / ")) +
      (track.album ? ` · ${track.album}` : "");
    const cover = $("#nowCover");
    const art = safeCover(track.cover);
    cover.style.backgroundImage = art ? `url("${art}")` : "none";
    document.title = `${track.title} · MusicBox`;
  }

  function updatePlayButton() {
    $("#playBtn").textContent = state.playing ? "⏸" : "▶";
  }

  $("#playBtn").addEventListener("click", async () => {
    if (!audio.src) {
      if (state.queue.length) {
        state.index = state.index < 0 ? 0 : state.index;
        await playCurrent();
      }
      return;
    }
    if (audio.paused) {
      await audio.play().catch((e) => toast(`播放失败：${e.message}`));
      state.playing = true;
    } else {
      audio.pause();
      state.playing = false;
    }
    updatePlayButton();
  });

  function step(delta) {
    if (!state.queue.length) return;
    if (state.mode === "shuffle") {
      let next = state.index;
      while (state.queue.length > 1 && next === state.index) {
        next = Math.floor(Math.random() * state.queue.length);
      }
      state.index = next;
    } else {
      state.index = (state.index + delta + state.queue.length) % state.queue.length;
    }
    playCurrent();
  }

  $("#prevBtn").addEventListener("click", () => step(-1));
  $("#nextBtn").addEventListener("click", () => step(1));

  $("#modeBtn").addEventListener("click", () => {
    const order = ["loop", "one", "shuffle"];
    state.mode = order[(order.indexOf(state.mode) + 1) % order.length];
    localStorage.setItem("mb.mode", state.mode);
    $("#modeBtn").textContent = { loop: "🔁", one: "🔂", shuffle: "🔀" }[state.mode];
    toast({ loop: "列表循环", one: "单曲循环", shuffle: "随机播放" }[state.mode]);
  });
  $("#modeBtn").textContent = { loop: "🔁", one: "🔂", shuffle: "🔀" }[state.mode];

  audio.addEventListener("ended", () => {
    if (state.mode === "one") {
      audio.currentTime = 0;
      audio.play().catch(() => {});
    } else {
      step(1);
    }
  });
  audio.addEventListener("pause", () => { state.playing = false; updatePlayButton(); });
  audio.addEventListener("play", () => { state.playing = true; updatePlayButton(); });
  audio.addEventListener("timeupdate", () => {
    const dur = audio.duration || 0;
    if (dur) {
      $("#progress").value = String(Math.round((audio.currentTime / dur) * 1000));
      $("#curTime").textContent = fmtTime(audio.currentTime);
      $("#durTime").textContent = fmtTime(dur);
    }
    syncLyrics(audio.currentTime);
  });
  const fallbackTried = new Set();

  audio.addEventListener("error", async () => {
    if (!audio.src) return;
    state.playing = false;
    updatePlayButton();
    const track = state.queue[state.index];
    if (!track) return;
    const key = `${track.provider}:${track.id}`;
    if (fallbackTried.has(key)) {
      toast("这首歌暂时无法播放（可能需要在「账号」里绑定会员账号）", 4200);
      return;
    }
    fallbackTried.add(key);
    toast("当前平台无法播放，正在寻找其它平台的同一首歌…", 3000);
    const alt = await findAlternative(track);
    if (alt) {
      // swap in the alternative without losing the queue position
      state.queue[state.index] = alt;
      toast(`已切换到${providerName(alt.provider)}`);
      playCurrent();
    } else {
      toast("这首歌暂时无法播放（可能需要在「账号」里绑定会员账号）", 4200);
    }
  });

  async function findAlternative(track) {
    try {
      const params = new URLSearchParams({
        title: track.title || "",
        artist: (track.artists || [])[0] || "",
        exclude: track.provider,
        limit: "5",
      });
      const data = await api(`/api/fallback?${params}`);
      for (const candidate of data.tracks || []) {
        try {
          const q = new URLSearchParams({ quality: state.quality });
          if (candidate.duration) q.set("duration", String(candidate.duration));
          const extra = candidate.extra || {};
          if (extra.hash320) q.set("hash320", extra.hash320);
          if (extra.hashFlac) q.set("hashFlac", extra.hashFlac);
          if (extra.hasLossless) q.set("hasLossless", "true");
          await api(`/api/stream/${candidate.provider}/${encodeURIComponent(candidate.id)}?${q}`);
          return candidate;
        } catch (_) {
          continue;
        }
      }
    } catch (_) {
      /* fall through */
    }
    return null;
  }

  $("#progress").addEventListener("input", (e) => {
    if (!audio.duration) return;
    audio.currentTime = (Number(e.target.value) / 1000) * audio.duration;
  });
  $("#volume").addEventListener("input", (e) => {
    audio.volume = Number(e.target.value) / 100;
  });
  audio.volume = 0.85;

  /* -------------------------------------------------------------- lyrics */
  async function loadLyrics(track) {
    const box = $("#lyricsInner");
    state.lyrics = [];
    state.lyricIndex = -1;
    box.innerHTML = '<p class="muted small">歌词加载中…</p>';
    try {
      const data = await api(
        `/api/lyrics/${encodeURIComponent(track.provider)}/${encodeURIComponent(track.id)}`
      );
      state.lyrics = data.lines || [];
      if (!state.lyrics.length) {
        box.innerHTML = '<p class="muted small">暂无歌词</p>';
        return;
      }
      box.innerHTML = state.lyrics
        .map((l, i) => `<p data-i="${i}">${esc(l.text) || "♪"}</p>`)
        .join("");
    } catch (err) {
      box.innerHTML = '<p class="muted small">暂无歌词</p>';
    }
  }

  function syncLyrics(t) {
    if (!state.lyrics.length) return;
    let idx = -1;
    for (let i = 0; i < state.lyrics.length; i += 1) {
      if (state.lyrics[i].time <= t + 0.15) idx = i;
      else break;
    }
    if (idx === state.lyricIndex) return;
    state.lyricIndex = idx;
    $$("#lyricsInner p").forEach((p, i) => p.classList.toggle("active", i === idx));
    const active = $(`#lyricsInner p[data-i="${idx}"]`);
    if (active) {
      const offset = active.offsetTop - $("#lyricsBox").clientHeight / 2 + active.offsetHeight / 2;
      $("#lyricsInner").style.transform = `translateY(${-offset}px)`;
    }
  }

  /* ----------------------------------------------------------- playlists */
  async function loadPlaylists() {
    try {
      const { playlists } = await api("/api/playlists");
      const box = $("#playlistList");
      box.innerHTML = "";
      if (!playlists.length) {
        box.innerHTML = '<div class="empty">还没有歌单，先新建一个</div>';
        $("#playlistTracks").innerHTML = "";
        return;
      }
      playlists.forEach((pl) => {
        const card = document.createElement("div");
        card.className = "pl-card" + (state.currentPlaylist === pl.id ? " active" : "");
        card.innerHTML = `<span>${esc(pl.name)}</span><span class="count">${pl.count}</span>
          <button title="删除歌单" data-act="del">✕</button>`;
        card.addEventListener("click", async (e) => {
          if (e.target.dataset.act === "del") {
            if (!confirm(`删除歌单「${pl.name}」？`)) return;
            await api(`/api/playlists/${pl.id}`, { method: "DELETE" });
            if (state.currentPlaylist === pl.id) state.currentPlaylist = null;
            loadPlaylists();
            return;
          }
          state.currentPlaylist = pl.id;
          await openPlaylist(pl);
        });
        box.appendChild(card);
      });
      if (state.currentPlaylist) {
        const current = playlists.find((p) => p.id === state.currentPlaylist);
        if (current) await openPlaylist(current, false);
      }
    } catch (err) {
      toast(`歌单加载失败：${err.message}`);
    }
  }

  async function openPlaylist(pl, refresh = true) {
    if (refresh) loadPlaylists();
    const data = await api(`/api/playlists/${pl.id}`);
    $("#playlistMeta").textContent = `${pl.name} · ${data.tracks.length} 首`;
    renderTracks($("#playlistTracks"), data.tracks, { context: "playlist" });
  }

  $("#newPlaylistForm").addEventListener("submit", async (e) => {
    e.preventDefault();
    const name = $("#newPlaylistName").value.trim();
    if (!name) return;
    await api("/api/playlists", { method: "POST", body: JSON.stringify({ name }) });
    $("#newPlaylistName").value = "";
    toast(`已创建「${name}」`);
    loadPlaylists();
  });

  /* ------------------------------------------------------------- history */
  async function loadHistory() {
    try {
      const { history } = await api("/api/history?limit=100");
      renderTracks($("#historyList"), history, { context: "history" });
      if (!history.length) $("#historyList").innerHTML = '<div class="empty">还没有播放记录</div>';
    } catch (err) {
      toast(`历史加载失败：${err.message}`);
    }
  }

  $("#clearHistory").addEventListener("click", async () => {
    if (!confirm("清空本地播放记录？")) return;
    await api("/api/history", { method: "DELETE" });
    loadHistory();
    toast("已清空播放记录");
  });

  /* ------------------------------------------------------------ accounts */
  function renderAccounts() {
    const box = $("#accountList");
    if (!box) return;
    box.innerHTML = "";
    state.providers.forEach((p) => {
      const el = document.createElement("div");
      el.className = "account";
      const methods = (p.login_methods || ["cookie"]).join(" / ");
      el.innerHTML = `
        <header>
          <span class="dot" style="background:${safeColor(p.color)}"></span>
          <span class="name">${esc(p.name)}</span>
          <span class="state ${p.loggedIn ? "on" : ""}">
            ${p.loggedIn
              ? (p.readyForVip ? `已绑定${p.uin ? " · uin " + esc(p.uin) : ""}` : "已绑定 · 缺少会员凭证")
              : "未绑定"}
          </span>
        </header>
        <p class="note">${esc(p.note || "")}</p>
        ${p.loggedIn && !p.readyForVip
          ? '<p class="note warn">Cookie 里没有找到会员凭证字段，VIP 歌曲可能仍然无法播放。建议在已登录会员的状态下重新复制一次完整 Cookie。</p>'
          : ""}
        <div class="row">
          <input type="password" placeholder="粘贴 Cookie（${esc(methods)}）" data-cookie />
          <button data-act="save">保存</button>
          ${p.loggedIn ? '<button class="danger" data-act="clear">解绑</button>' : ""}
        </div>`;
      el.querySelector('[data-act="save"]').addEventListener("click", async () => {
        const input = el.querySelector("[data-cookie]");
        const cookie = input.value.trim();
        if (!cookie) return toast("请先粘贴 Cookie");
        try {
          const res = await api(`/api/account/${p.id}`, {
            method: "POST",
            body: JSON.stringify({ cookie }),
          });
          input.value = "";
          toast(`${p.name} 绑定成功（${res.cookieCount} 个 Cookie）`);
          await loadProviders();
        } catch (err) {
          toast(`绑定失败：${err.message}`, 4000);
        }
      });
      const clearBtn = el.querySelector('[data-act="clear"]');
      if (clearBtn) {
        clearBtn.addEventListener("click", async () => {
          if (!confirm(`解绑 ${p.name} 账号？本地保存的 Cookie 会被删除。`)) return;
          await api(`/api/account/${p.id}`, { method: "DELETE" });
          toast(`已解绑 ${p.name}`);
          await loadProviders();
        });
      }
      box.appendChild(el);
    });
  }

  async function loadAccounts() {
    await loadProviders();
  }

  /* ------------------------------------------------------------- privacy */
  $("#openPrivacy").addEventListener("click", async () => {
    const ok = confirm(
      "隐私说明：\n\n" +
        "· 所有数据只保存在本机（" + "本地数据目录" + "），不上传、不统计。\n" +
        "· 账号 Cookie 使用 AES + HMAC 加密存储。\n" +
        "· 音频由本机服务器代理转发，页面拿不到带 token 的直链。\n\n" +
        "点击「确定」可清空全部本地数据（账号、歌单、历史）。"
    );
    if (!ok) return;
    if (!confirm("最后确认：这会删除所有已绑定的账号和歌单，无法恢复。")) return;
    try {
      await api("/api/privacy/wipe", { method: "POST", body: JSON.stringify({ confirm: true }) });
      toast("本地数据已清空");
      state.queue = [];
      state.index = -1;
      audio.pause();
      audio.removeAttribute("src");
      $("#nowTitle").textContent = "未在播放";
      $("#nowArtist").textContent = "选择一首歌开始";
      $("#nowCover").style.backgroundImage = "none";
      $("#lyricsInner").innerHTML = '<p class="muted small">歌词会显示在这里</p>';
      document.title = "MusicBox · 聚合音乐播放器";
      await loadProviders();
      showView("search");
    } catch (err) {
      toast(`清空失败：${err.message}`);
    }
  });

  /* --------------------------------------------------------- keyboard */
  document.addEventListener("keydown", (e) => {
    const tag = (e.target.tagName || "").toLowerCase();
    if (tag === "input" || tag === "textarea" || tag === "select") return;
    if (e.code === "Space") {
      e.preventDefault();
      $("#playBtn").click();
    }
    if (e.code === "ArrowRight" && e.shiftKey) step(1);
    if (e.code === "ArrowLeft" && e.shiftKey) step(-1);
  });

  /* -------------------------------------------------------------- boot */
  loadProviders();
  fetch("/api/health").then((r) => r.json()).then(() => {}).catch(() => {});
})();
