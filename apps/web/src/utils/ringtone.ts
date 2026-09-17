/**
 * Web Audio API based Ringtone Engine
 * Synthesizes standard outgoing ringback tones and incoming call ringtones.
 */

class RingtoneManager {
  private ctx: AudioContext | null = null;
  private isRinging: boolean = false;
  private ringTimer: any = null;

  private getAudioContext(): AudioContext {
    if (!this.ctx || this.ctx.state === "closed") {
      const AudioCtx = window.AudioContext || (window as any).webkitAudioContext;
      this.ctx = new AudioCtx();
    }
    if (this.ctx.state === "suspended") {
      this.ctx.resume();
    }
    return this.ctx;
  }

  /**
   * Play outgoing call ringback tone (Standard North American / European dual tone)
   */
  startOutgoingRing() {
    if (this.isRinging) this.stop();
    this.isRinging = true;

    const playCycle = () => {
      if (!this.isRinging) return;
      try {
        const ctx = this.getAudioContext();
        const now = ctx.currentTime;

        const osc1 = ctx.createOscillator();
        const osc2 = ctx.createOscillator();
        const gain = ctx.createGain();

        osc1.type = "sine";
        osc2.type = "sine";
        osc1.frequency.setValueAtTime(440, now);
        osc2.frequency.setValueAtTime(480, now);

        // 2 seconds tone, then silence
        gain.gain.setValueAtTime(0.001, now);
        gain.gain.linearRampToValueAtTime(0.12, now + 0.05);
        gain.gain.setValueAtTime(0.12, now + 1.95);
        gain.gain.linearRampToValueAtTime(0.001, now + 2.0);

        osc1.connect(gain);
        osc2.connect(gain);
        gain.connect(ctx.destination);

        osc1.start(now);
        osc2.start(now);
        osc1.stop(now + 2.05);
        osc2.stop(now + 2.05);

        this.ringTimer = setTimeout(() => {
          if (this.isRinging) playCycle();
        }, 4000); // 2s ring + 2s pause
      } catch (err) {
        console.warn("[ringtone] outgoing ring error:", err);
      }
    };

    playCycle();
  }

  /**
   * Play incoming call ringtone (Pleasant modern digital chime)
   */
  startIncomingRing() {
    if (this.isRinging) this.stop();
    this.isRinging = true;

    const notes = [
      { freq: 587.33, start: 0, dur: 0.15 }, // D5
      { freq: 659.25, start: 0.18, dur: 0.15 }, // E5
      { freq: 880.0, start: 0.36, dur: 0.25 }, // A5
      { freq: 783.99, start: 0.65, dur: 0.35 }, // G5
      { freq: 587.33, start: 1.1, dur: 0.15 }, // D5
      { freq: 659.25, start: 1.28, dur: 0.15 }, // E5
      { freq: 880.0, start: 1.46, dur: 0.3 }, // A5
      { freq: 1046.5, start: 1.8, dur: 0.45 }, // C6
    ];

    const playMelody = () => {
      if (!this.isRinging) return;
      try {
        const ctx = this.getAudioContext();
        const now = ctx.currentTime;

        notes.forEach(({ freq, start, dur }) => {
          const osc = ctx.createOscillator();
          const gain = ctx.createGain();

          osc.type = "sine";
          osc.frequency.setValueAtTime(freq, now + start);

          gain.gain.setValueAtTime(0.001, now + start);
          gain.gain.linearRampToValueAtTime(0.18, now + start + 0.02);
          gain.gain.exponentialRampToValueAtTime(0.001, now + start + dur);

          osc.connect(gain);
          gain.connect(ctx.destination);

          osc.start(now + start);
          osc.stop(now + start + dur + 0.05);
        });

        this.ringTimer = setTimeout(() => {
          if (this.isRinging) playMelody();
        }, 3000);
      } catch (err) {
        console.warn("[ringtone] incoming ring error:", err);
      }
    };

    playMelody();
  }

  stop() {
    this.isRinging = false;
    if (this.ringTimer) {
      clearTimeout(this.ringTimer);
      this.ringTimer = null;
    }
  }
}

export const ringtone = new RingtoneManager();
