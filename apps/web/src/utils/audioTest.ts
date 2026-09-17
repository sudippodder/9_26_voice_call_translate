/**
 * Audio test helper using Web Audio API + SpeechSynthesis
 * Plays a pleasant 3-tone chime and spoken confirmation to verify speaker output.
 */
export async function playTestSound(): Promise<void> {
  const AudioContextClass = window.AudioContext || (window as any).webkitAudioContext;
  if (!AudioContextClass) {
    throw new Error("Web Audio API not supported by this browser");
  }

  const ctx = new AudioContextClass();
  if (ctx.state === "suspended") {
    await ctx.resume();
  }

  const now = ctx.currentTime;
  const frequencies = [523.25, 659.25, 783.99, 1046.5]; // C5, E5, G5, C6
  const duration = 0.15;

  frequencies.forEach((freq, i) => {
    const osc = ctx.createOscillator();
    const gain = ctx.createGain();

    osc.type = "sine";
    osc.frequency.setValueAtTime(freq, now + i * duration);

    // Smooth ADSR envelope
    gain.gain.setValueAtTime(0.001, now + i * duration);
    gain.gain.exponentialRampToValueAtTime(0.2, now + i * duration + 0.03);
    gain.gain.exponentialRampToValueAtTime(0.001, now + (i + 1) * duration);

    osc.connect(gain);
    gain.connect(ctx.destination);

    osc.start(now + i * duration);
    osc.stop(now + (i + 1) * duration + 0.05);
  });

  // Also play speech synthesis confirmation after the chime if available
  setTimeout(() => {
    try {
      if ("speechSynthesis" in window) {
        window.speechSynthesis.cancel();
        const utterance = new SpeechSynthesisUtterance("Audio test successful. Speaker is working.");
        utterance.rate = 1.0;
        utterance.pitch = 1.0;
        utterance.volume = 1.0;
        window.speechSynthesis.speak(utterance);
      }
    } catch {
      /* ignore speech synthesis errors */
    }
  }, (frequencies.length * duration + 0.1) * 1000);
}
