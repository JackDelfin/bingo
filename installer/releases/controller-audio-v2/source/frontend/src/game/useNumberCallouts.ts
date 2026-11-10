import { useCallback, useEffect, useRef, useState } from 'react';

/** Keep imperative media playback separate from React's immutable state. */
class CalloutPlayer {
  private audio: HTMLAudioElement | null = null;
  private queue: number[] = [];
  private playing = false;
  private currentNumber: number | null = null;
  private generation = 0;
  private onError: () => void;

  constructor(onError: () => void) {
    this.onError = onError;
  }

  stop() {
    this.generation += 1;
    this.queue = [];
    this.playing = false;
    this.currentNumber = null;
    const audio = this.audio;
    if (audio) {
      audio.onended = null;
      audio.onerror = null;
      audio.pause();
      audio.removeAttribute('src');
      audio.load();
    }
  }

  announce(number: number) {
    if (this.playing) this.queue.push(number);
    else this.play(number);
  }

  synchronize(numbers: readonly number[], bingo: boolean) {
    if (this.playing && (bingo
      || (this.currentNumber !== null && !numbers.includes(this.currentNumber))
      || this.queue.some((number) => !numbers.includes(number)))) {
      this.stop();
    }
  }

  private play(number: number) {
    const token = ++this.generation;
    const fail = () => {
      if (token !== this.generation) return;
      this.stop();
      this.onError();
    };
    try {
      const audio = this.audio ?? new Audio();
      this.audio = audio;
      this.playing = true;
      this.currentNumber = number;
      audio.src = `/audio/calls/${number}.mp3`;
      audio.onended = () => {
        if (token !== this.generation) return;
        const next = this.queue.shift();
        if (next === undefined) {
          this.playing = false;
          this.currentNumber = null;
        } else this.play(next);
      };
      audio.onerror = fail;
      void audio.play().catch(fail);
    } catch {
      fail();
    }
  }
}

/** Audio is local to this controller tab, never part of synchronized game state. */
export function useNumberCallouts(numbers: readonly number[], bingo: boolean) {
  const [enabled, updateEnabled] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const player = useRef<CalloutPlayer | null>(null);
  const stop = useCallback(() => player.current?.stop(), []);
  useEffect(() => stop, [stop]);
  useEffect(() => player.current?.synchronize(numbers, bingo), [numbers, bingo]);

  const setEnabled = (value: boolean) => {
    stop();
    setError(null);
    updateEnabled(value);
  };

  const announce = (number: number) => {
    if (!enabled) return;
    player.current ??= new CalloutPlayer(() => {
      updateEnabled(false);
      setError('Audio could not play. Check your browser and speakers, then turn Audio callouts on to retry.');
    });
    player.current.announce(number);
  };

  return { enabled, setEnabled, announce, stop, error };
}
