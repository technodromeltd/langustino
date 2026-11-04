// Forwards raw mic frames (Float32, context sample rate) to the main thread.
class RecorderProcessor extends AudioWorkletProcessor {
  process(inputs) {
    const ch = inputs[0] && inputs[0][0];
    if (ch) this.port.postMessage(ch.slice(0));
    return true;
  }
}
registerProcessor("recorder", RecorderProcessor);
