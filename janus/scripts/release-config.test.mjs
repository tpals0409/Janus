import assert from "node:assert/strict";
import { createRequire } from "node:module";
import test from "node:test";

const require = createRequire(import.meta.url);
const configPath = require.resolve("../electron-builder.config.cjs");

function loadConfig(signed) {
  const previous = process.env.JANUS_SIGNED_RELEASE;
  if (signed) process.env.JANUS_SIGNED_RELEASE = "1";
  else delete process.env.JANUS_SIGNED_RELEASE;
  delete require.cache[configPath];
  const config = require(configPath);
  if (previous === undefined) delete process.env.JANUS_SIGNED_RELEASE;
  else process.env.JANUS_SIGNED_RELEASE = previous;
  return config;
}

test("local mac package remains unsigned and unpacked", () => {
  const config = loadConfig(false);
  assert.deepEqual(config.mac.target, ["dir"]);
  assert.equal(config.mac.identity, null);
  assert.equal(config.mac.hardenedRuntime, false);
  assert.equal(config.mac.notarize, false);
});

test("public mac package requires hardened signing and notarization", () => {
  const config = loadConfig(true);
  assert.deepEqual(config.mac.target, ["dmg", "zip"]);
  assert.equal(config.mac.identity, undefined);
  assert.equal(config.mac.hardenedRuntime, true);
  assert.equal(config.mac.notarize, true);
  assert.equal(config.dmg.sign, true);
});
