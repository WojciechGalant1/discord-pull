// Cloudflare Worker: uruchamia bota według harmonogramu (Cron Trigger) i obsługuje komendy Discorda.
// Zmienne w ustawieniach Workera: DISCORD_PUBLIC_KEY, GITHUB_REPO (np. login/discord-pull), GITHUB_TOKEN (sekret).

const EPHEMERAL = 64;
const DEFAULT_INTERVAL_MINUTES = 10;
const PREFIXES = { reddit: "r/", user: "u/", redgifs: "redgifs/" };

export default {
  async fetch(request, env, ctx) {
    if (request.method !== "POST") {
      return new Response("discord-pull: Worker is running");
    }

    const signature = request.headers.get("X-Signature-Ed25519");
    const timestamp = request.headers.get("X-Signature-Timestamp");
    const body = await request.text();
    if (!signature || !timestamp || !(await isValidSignature(env.DISCORD_PUBLIC_KEY, signature, timestamp + body))) {
      return new Response("invalid request signature", { status: 401 });
    }

    const interaction = JSON.parse(body);
    if (interaction.type === 1) {
      return reply({ type: 1 });
    }
    if (interaction.type === 4) {
      return reply({ type: 8, data: { choices: await autocomplete(env, interaction) } });
    }
    if (interaction.type !== 2) {
      return message("Unknown interaction.");
    }

    switch (interaction.data.name) {
      case "when":
        return message(await safely(() => whenText(env, interaction)));
      case "list":
        return message(await safely(() => listText(env)));
      case "add":
        return deferred(ctx, interaction, () => addSource(env, interaction));
      case "remove":
        return deferred(ctx, interaction, () => removeSource(env, interaction));
      case "post":
        return deferred(ctx, interaction, () => postNow(env, interaction));
      default:
        return message("Unknown command.");
    }
  },

  async scheduled(event, env, ctx) {
    const minutes = Number(event.cron.match(/^\*\/(\d+)\s/)?.[1]) || DEFAULT_INTERVAL_MINUTES;
    ctx.waitUntil(dispatchWorkflow(env, { interval_minutes: String(minutes) }));
  },
};

// ---------- Discord ----------

async function isValidSignature(publicKey, signature, payload) {
  try {
    const key = await crypto.subtle.importKey("raw", fromHex(publicKey), { name: "Ed25519" }, false, ["verify"]);
    return await crypto.subtle.verify("Ed25519", key, fromHex(signature), new TextEncoder().encode(payload));
  } catch {
    return false;
  }
}

function fromHex(hex) {
  return new Uint8Array(hex.match(/../g).map((byte) => parseInt(byte, 16)));
}

function reply(data) {
  return new Response(JSON.stringify(data), { headers: { "Content-Type": "application/json" } });
}

function message(content) {
  return reply({ type: 4, data: { content: truncate(content), flags: EPHEMERAL, allowed_mentions: { parse: [] } } });
}

// Discord czeka na odpowiedź tylko 3 s, a zapis do GitHuba i sprawdzanie źródła mogą trwać dłużej
function deferred(ctx, interaction, work) {
  ctx.waitUntil(
    (async () => {
      const content = await safely(work);
      await fetch(`https://discord.com/api/v10/webhooks/${interaction.application_id}/${interaction.token}/messages/@original`, {
        method: "PATCH",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ content: truncate(content), allowed_mentions: { parse: [] } }),
      });
    })(),
  );
  return reply({ type: 5, data: { flags: EPHEMERAL } });
}

async function safely(work) {
  try {
    return await work();
  } catch (error) {
    return `Something went wrong: ${error.message}`;
  }
}

function truncate(content) {
  return content.length > 2000 ? content.slice(0, content.lastIndexOf("\n", 1990)) + "\n…" : content;
}

function option(interaction, name) {
  return interaction.data.options?.find((item) => item.name === name)?.value;
}

// ---------- GitHub ----------

function github(env, path, init = {}) {
  return fetch(`https://api.github.com/repos/${env.GITHUB_REPO}/${path}`, {
    ...init,
    headers: {
      Authorization: `Bearer ${env.GITHUB_TOKEN}`,
      Accept: "application/vnd.github+json",
      "User-Agent": "discord-pull-worker",
      ...init.headers,
    },
  });
}

async function readRepoJson(env, file, fallback) {
  const res = await github(env, `contents/${file}`);
  if (res.status === 404) {
    return { data: fallback, sha: undefined };
  }
  if (!res.ok) {
    throw new Error(`could not read ${file} (GitHub HTTP ${res.status})`);
  }
  const body = await res.json();
  const bytes = Uint8Array.from(atob(body.content.replace(/\n/g, "")), (char) => char.charCodeAt(0));
  return { data: JSON.parse(new TextDecoder().decode(bytes)), sha: body.sha };
}

async function loadSources(env) {
  const { data } = await readRepoJson(env, "sources.json", { sources: [] });
  return data.repository && data.repository !== env.GITHUB_REPO ? [] : data.sources || [];
}

// Zmienia sources.json; ponawia, gdy w międzyczasie ktoś inny zapisał plik
async function updateSources(env, change, commitMessage) {
  for (let attempt = 0; attempt < 3; attempt++) {
    const { data, sha } = await readRepoJson(env, "sources.json", { sources: [] });
    const current = data.repository && data.repository !== env.GITHUB_REPO ? [] : data.sources || [];
    const result = change(current);
    if (!result.changed) {
      return result.text;
    }
    const json = JSON.stringify({ repository: env.GITHUB_REPO, sources: result.sources }, null, 2) + "\n";
    let binary = "";
    for (const byte of new TextEncoder().encode(json)) {
      binary += String.fromCharCode(byte);
    }
    const res = await github(env, "contents/sources.json", {
      method: "PUT",
      body: JSON.stringify({ message: `${commitMessage} [skip ci]`, content: btoa(binary), sha }),
    });
    if (res.ok) {
      return result.text;
    }
    if (res.status !== 409 && res.status !== 422) {
      throw new Error(`could not save sources.json (GitHub HTTP ${res.status})`);
    }
  }
  throw new Error("sources.json kept changing, try again");
}

async function dispatchWorkflow(env, inputs) {
  const res = await github(env, "actions/workflows/run.yml/dispatches", {
    method: "POST",
    body: JSON.stringify({ ref: "main", inputs }),
  });
  if (!res.ok) {
    throw new Error(`could not start the bot (GitHub HTTP ${res.status})`);
  }
}

// ---------- Źródła ----------

function label(source) {
  return PREFIXES[source.type] + source.name;
}

function parseSource(text) {
  const value = text.trim();
  let match = value.match(/redgifs\.com\/niches\/([A-Za-z0-9_-]+)/i);
  if (match) return { type: "redgifs", name: match[1].toLowerCase() };
  match = value.match(/reddit\.com\/r\/([A-Za-z0-9_]+)/i);
  if (match) return { type: "reddit", name: match[1] };
  match = value.match(/reddit\.com\/(?:u|user)\/([A-Za-z0-9_-]+)/i);
  if (match) return { type: "user", name: match[1] };
  match = value.replace(/^\//, "").match(/^(r|u|user|redgifs)[/:]([A-Za-z0-9_-]+)\/?$/i);
  if (!match) return null;
  const prefix = match[1].toLowerCase();
  if (prefix === "redgifs") return { type: "redgifs", name: match[2].toLowerCase() };
  return { type: prefix === "r" ? "reddit" : "user", name: match[2] };
}

// true – źródło ma treści, false – nie istnieje albo jest puste, null – nie udało się sprawdzić
async function sourceExists(source) {
  try {
    if (source.type === "redgifs") {
      const auth = await fetch("https://api.redgifs.com/v2/auth/temporary");
      const { token } = await auth.json();
      const res = await fetch(`https://api.redgifs.com/v2/niches/${encodeURIComponent(source.name)}/gifs?count=1`, {
        headers: { Authorization: `Bearer ${token}` },
      });
      if (res.status === 404) return false;
      return res.ok ? ((await res.json()).gifs || []).length > 0 : null;
    }
    const field = source.type === "reddit" ? "subreddit" : "author";
    const res = await fetch(
      `https://arctic-shift.photon-reddit.com/api/posts/search?${field}=${encodeURIComponent(source.name)}&limit=1`,
    );
    return res.ok ? ((await res.json()).data || []).length > 0 : null;
  } catch {
    return null;
  }
}

function sameSource(a, b) {
  return a.type === b.type && a.name.toLowerCase() === b.name.toLowerCase();
}

async function addSource(env, interaction) {
  const source = parseSource(option(interaction, "source") || "");
  if (!source) {
    return "Unknown source. Use `r/subreddit`, `u/username`, `redgifs/niche` or a full link.";
  }
  const channelId = option(interaction, "channel") || interaction.channel_id;
  const channelName =
    interaction.data.resolved?.channels?.[channelId]?.name ||
    (channelId === interaction.channel_id ? interaction.channel?.name : "") ||
    "";

  if ((await sourceExists(source)) === false) {
    return `Found no posts for \`${label(source)}\`. Check the name.`;
  }

  return updateSources(
    env,
    (sources) => {
      if (sources.some((item) => sameSource(item, source) && item.channel_id === channelId)) {
        return { changed: false, text: `\`${label(source)}\` already posts in <#${channelId}>.` };
      }
      return {
        changed: true,
        sources: [...sources, { ...source, channel_id: channelId, channel_name: channelName }],
        text: `Added \`${label(source)}\` → <#${channelId}>. It will post when its turn comes (see /when), or right away with /post.`,
      };
    },
    `Add ${label(source)}`,
  );
}

async function removeSource(env, interaction) {
  const [text, channelId] = (option(interaction, "source") || "").split("|");
  const source = parseSource(text);
  if (!source) {
    return "Unknown source. Pick one from the list.";
  }
  return updateSources(
    env,
    (sources) => {
      const kept = sources.filter((item) => !(sameSource(item, source) && (!channelId || item.channel_id === channelId)));
      if (kept.length === sources.length) {
        return { changed: false, text: `\`${label(source)}\` is not on the list.` };
      }
      return { changed: true, sources: kept, text: `Removed \`${label(source)}\`${channelId ? ` from <#${channelId}>` : ""}.` };
    },
    `Remove ${label(source)}`,
  );
}

async function postNow(env, interaction) {
  const source = parseSource(option(interaction, "source") || "");
  const sources = await loadSources(env);
  if (!source || !sources.some((item) => sameSource(item, source))) {
    return "This source is not on the list. Add it first with /add.";
  }
  const name = label(sources.find((item) => sameSource(item, source)));
  await dispatchWorkflow(env, { source: name });
  return `Started \`${name}\`. New posts should appear in about 1–2 minutes (later if a scheduled run is in progress).`;
}

async function autocomplete(env, interaction) {
  const typed = (interaction.data.options?.find((item) => item.focused)?.value || "").toLowerCase();
  let sources = [];
  try {
    sources = await loadSources(env);
  } catch {
    return [];
  }
  const matching = sources.filter((source) => label(source).toLowerCase().includes(typed));

  if (interaction.data.name === "remove") {
    return matching.slice(0, 25).map((source) => ({
      name: `${label(source)} → #${source.channel_name || source.channel_id}`.slice(0, 100),
      value: `${label(source)}|${source.channel_id}`,
    }));
  }
  const names = [...new Set(matching.map(label))];
  return names.slice(0, 25).map((name) => ({ name, value: name }));
}

// ---------- /list i /when ----------

async function listText(env) {
  const sources = await loadSources(env);
  if (!sources.length) {
    return "No sources yet. Add one with /add.";
  }
  return [`**Sources (${sources.length}):**`, ...sources.map((source) => `\`${label(source)}\` → <#${source.channel_id}>`)].join("\n");
}

async function whenText(env, interaction) {
  const { data: schedule } = await readRepoJson(env, "schedule.json", null);
  if (!schedule) {
    return "No schedule yet. It appears after the bot's first run.";
  }

  const now = Math.floor(Date.now() / 1000);
  const interval = (schedule.interval_minutes || DEFAULT_INTERVAL_MINUTES) * 60;
  // Cron Trigger uruchamia bota w pełnych wielokrotnościach interwału; jeśli przebieg z bieżącego terminu
  // jeszcze się nie zapisał, kolejka w schedule.json jest o jeden przebieg do tyłu
  const currentTick = Math.floor(now / interval) * interval;
  const base = (schedule.last_run < currentTick ? currentTick : currentTick + interval) + 60;

  const channelId = option(interaction, "channel") || interaction.channel_id;
  const forChannel = schedule.sources.filter((source) => source.channel_id === channelId);
  const sources = forChannel.length ? forChannel : schedule.sources;

  const lines = [forChannel.length ? `**Next posts in <#${channelId}>:**` : "**Next posts:**"];
  for (const source of sources) {
    const at = base + source.runs_ahead * interval;
    const when = at <= now ? "now" : `<t:${at}:R>`;
    lines.push(forChannel.length ? `\`${source.source}\` ${when}` : `\`${source.source}\` → <#${source.channel_id}> ${when}`);
  }
  lines.push("", `Last bot run: <t:${schedule.last_run}:R>. Times are approximate.`);
  if (now - schedule.last_run > 3 * interval) {
    lines.push("⚠️ The bot has not run for a while. Check the Actions tab on GitHub.");
  }
  return lines.join("\n");
}
