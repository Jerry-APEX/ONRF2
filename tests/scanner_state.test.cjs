// Camera-state regression checks without hardware. Run: node --test tests/scanner_state.test.cjs
const { test } = require("node:test");
const assert = require("node:assert/strict");
const vm = require("node:vm");
const fs = require("node:fs");

function setup() {
  let stamp = 0,
    current = "",
    canRead = true,
    decoded = 0;
  const context = {
    window: {},
    document: {
      hidden: false,
      createElement: () => ({ getContext: () => ({}) }),
    },
    performance: { now: () => stamp },
    requestAnimationFrame: () => 1,
    cancelAnimationFrame: () => {},
  };
  vm.createContext(context);
  vm.runInContext(fs.readFileSync("static/scanner.js", "utf8"), context);
  const calls = [];
  const video = { readyState: 4, currentTime: 0 };
  const camera = new context.window.TicketCamera({
    video,
    onRead: async (value) => calls.push(value),
    onState: () => {},
    canRead: () => canRead,
  });
  camera.running = true;
  camera.generation = 1;
  camera.lastFrameAt = 0;
  camera.lastVideoTime = -1;
  camera.decodeCanvas = () => {
    decoded++;
    return current;
  };
  return {
    camera,
    calls,
    video,
    get decoded() {
      return decoded;
    },
    set ready(value) {
      canRead = value;
    },
    async step(value, advance = 200) {
      stamp += advance;
      current = value;
      video.currentTime += advance / 1000;
      await camera.tick(stamp, 1);
    },
  };
}

test("A stationary QR does not resubmit after eight seconds; removal re-arms it", async () => {
  const fixture = setup();
  for (let i = 0; i < 60; i++) await fixture.step("sample");
  assert.deepEqual(fixture.calls, ["sample"]);
  for (let i = 0; i < 13; i++) await fixture.step("");
  await fixture.step("sample");
  await fixture.step("sample");
  assert.deepEqual(fixture.calls, ["sample", "sample"]);
});

test("A slow verification followed by one empty frame does not release the QR latch", async () => {
  const fixture = setup();
  await fixture.step("sample");
  await fixture.step("sample");
  await fixture.step("", 15000);
  await fixture.step("sample");
  await fixture.step("sample");
  assert.deepEqual(fixture.calls, ["sample"]);
});

test("Frozen video frames are skipped and manual verification pauses recognition", async () => {
  const fixture = setup();
  await fixture.step("sample");
  const count = fixture.decoded;
  await fixture.camera.tick(400, 1);
  assert.equal(fixture.decoded, count);
  fixture.ready = false;
  await fixture.step("other");
  await fixture.step("other");
  assert.equal(fixture.decoded, count);
  fixture.ready = true;
  await fixture.step("other");
  await fixture.step("other");
  assert.deepEqual(fixture.calls, ["other"]);
});
