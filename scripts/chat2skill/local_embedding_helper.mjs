#!/usr/bin/env node

if (!process.env.ORT_LOG_LEVEL) {
  process.env.ORT_LOG_LEVEL = "3";
}

const DEFAULT_MODEL = "Snowflake/snowflake-arctic-embed-xs";
const DEFAULT_DIMS = 384;

async function readStdin() {
  let input = "";
  for await (const chunk of process.stdin) {
    input += chunk;
  }
  return input;
}

function tensorToVectors(result, count, dimensions) {
  const data = Array.from(result.data || []);
  if (count === 1) {
    return [data];
  }
  const vectors = [];
  for (let i = 0; i < count; i += 1) {
    vectors.push(data.slice(i * dimensions, (i + 1) * dimensions));
  }
  return vectors;
}

async function main() {
  const raw = await readStdin();
  const payload = raw ? JSON.parse(raw) : {};
  const texts = Array.isArray(payload.texts) ? payload.texts.map((item) => String(item)) : [];
  if (texts.length === 0) {
    process.stdout.write(JSON.stringify({ vectors: [] }));
    return;
  }

  const model = payload.model || DEFAULT_MODEL;
  const dimensions = Number.parseInt(String(payload.dimensions || DEFAULT_DIMS), 10);
  if (!Number.isFinite(dimensions) || dimensions <= 0) {
    throw new Error(`invalid embedding dimensions: ${payload.dimensions}`);
  }

  const { pipeline, env } = await import("@huggingface/transformers");
  env.allowLocalModels = false;
  if (process.env.CHAT2SKILL_EMBEDDING_CACHE_DIR) {
    env.cacheDir = process.env.CHAT2SKILL_EMBEDDING_CACHE_DIR;
  } else if (process.env.HF_HOME) {
    env.cacheDir = process.env.HF_HOME;
  }
  if (process.env.HF_ENDPOINT) {
    env.remoteHost = process.env.HF_ENDPOINT.replace(/\/+$/, "");
  }

  const embedder = await pipeline("feature-extraction", model, {
    device: "cpu",
    dtype: "fp32",
    session_options: {
      logSeverityLevel: 3,
      intraOpNumThreads: Number.parseInt(process.env.CHAT2SKILL_EMBEDDING_THREADS || "2", 10),
      interOpNumThreads: 1,
      executionMode: "sequential",
    },
  });
  const result = await embedder(texts.length === 1 ? texts[0] : texts, {
    pooling: "mean",
    normalize: true,
  });
  process.stdout.write(JSON.stringify({ vectors: tensorToVectors(result, texts.length, dimensions) }));
}

main().catch((error) => {
  const message = error instanceof Error ? error.message : String(error);
  process.stderr.write(message);
  process.exit(1);
});
