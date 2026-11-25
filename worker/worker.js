// Cloudflare Worker obsługujący komendę /when.
// Zmienne w ustawieniach Workera: DISCORD_PUBLIC_KEY, GITHUB_REPO (np. login/discord-pull), GITHUB_TOKEN (sekret).

const EPHEMERAL = 64;

export default {
  async fetch(request, env) {
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
    if (interaction.type === 2 && interaction.data.name === "when") {
      return message(await scheduleText(env, interaction));
    }
    return message("Unknown command.");
  },
};

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
  return reply({ type: 4, data: { content, flags: EPHEMERAL, allowed_mentions: { parse: [] } } });
}

async function scheduleText(env, interaction) {
  const res = await fetch(`https://api.github.com/repos/${env.GITHUB_REPO}/contents/schedule.json`, {
    headers: {
      Authorization: `Bearer ${env.GITHUB_TOKEN}`,
      Accept: "application/vnd.github.raw+json",
      "User-Agent": "discord-pull-worker",
    },
  });
  if (!res.ok) {
    return `Could not read the schedule (GitHub HTTP ${res.status}).`;
  }
  const schedule = await res.json();

  const now = Math.floor(Date.now() / 1000);
  const interval = schedule.interval_minutes * 60;
  // Gdy GitHub spóźnia się z uruchomieniem, następny przebieg liczymy od teraz, a nie od zaległego terminu
  const nextRun = Math.max(schedule.last_run + interval, now + 60);

  const chosen = interaction.data.options?.find((option) => option.name === "channel")?.value;
  const channelId = chosen || interaction.channel_id;
  const forChannel = schedule.sources.filter((source) => source.channel_id === channelId);
  const sources = forChannel.length ? forChannel : schedule.sources;

  const lines = [forChannel.length ? `**Next posts in <#${channelId}>:**` : "**Next posts:**"];
  for (const source of sources) {
    const when = `<t:${nextRun + source.runs_ahead * interval}:R>`;
    lines.push(forChannel.length ? `\`${source.source}\` ${when}` : `\`${source.source}\` → <#${source.channel_id}> ${when}`);
  }
  lines.push("", `Last bot run: <t:${schedule.last_run}:R>. Times are approximate.`);

  let content = lines.join("\n");
  if (content.length > 2000) {
    content = content.slice(0, content.lastIndexOf("\n", 1990)) + "\n…";
  }
  return content;
}
