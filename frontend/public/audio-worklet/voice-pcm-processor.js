class VoicePcmProcessor extends AudioWorkletProcessor {
  constructor() {
    super();
    this.targetSampleRate = 16000;
    this.samplesPerChunk = 1600;
    this.pending = [];
    this.pendingLength = 0;
  }

  process(inputs) {
    const input = inputs[0]?.[0];
    if (!input || input.length === 0) {
      return true;
    }
    const resampled = this.resample(input, sampleRate, this.targetSampleRate);
    this.enqueue(this.float32ToInt16(resampled));
    this.flushChunks();
    return true;
  }

  resample(input, sourceRate, targetRate) {
    if (sourceRate === targetRate) {
      return input;
    }
    const ratio = sourceRate / targetRate;
    const outputLength = Math.max(1, Math.round(input.length / ratio));
    const output = new Float32Array(outputLength);
    for (let index = 0; index < outputLength; index += 1) {
      const sourceIndex = index * ratio;
      const lower = Math.floor(sourceIndex);
      const upper = Math.min(lower + 1, input.length - 1);
      const weight = sourceIndex - lower;
      output[index] = input[lower] * (1 - weight) + input[upper] * weight;
    }
    return output;
  }

  float32ToInt16(input) {
    const output = new Int16Array(input.length);
    for (let index = 0; index < input.length; index += 1) {
      const value = Math.max(-1, Math.min(1, input[index]));
      output[index] = value < 0 ? value * 0x8000 : value * 0x7fff;
    }
    return output;
  }

  enqueue(pcm) {
    this.pending.push(pcm);
    this.pendingLength += pcm.length;
  }

  flushChunks() {
    while (this.pendingLength >= this.samplesPerChunk && this.pending.length > 0) {
      const chunk = new Int16Array(this.samplesPerChunk);
      let offset = 0;
      while (offset < this.samplesPerChunk && this.pending.length > 0) {
        const head = this.pending[0];
        const take = Math.min(head.length, this.samplesPerChunk - offset);
        chunk.set(head.subarray(0, take), offset);
        if (take === head.length) {
          this.pending.shift();
        } else {
          this.pending[0] = head.subarray(take);
        }
        offset += take;
        this.pendingLength -= take;
      }
      this.port.postMessage(chunk.buffer, [chunk.buffer]);
    }
  }
}

registerProcessor('voice-pcm-processor', VoicePcmProcessor);
