import type { Settings } from "./types";

export function SettingsPanel({
  settings,
  voices,
  voice,
  onSettings,
  onVoice,
  sounds,
  onSounds,
  onClose,
}: {
  settings: Settings;
  voices: { id: string; label: string }[];
  voice: string;
  onSettings: (patch: Partial<Settings>) => void;
  onVoice: (id: string) => void;
  sounds: boolean;
  onSounds: (on: boolean) => void;
  onClose: () => void;
}) {
  const choices = settings.goal_choices?.length ? settings.goal_choices : [10, 20, 30, 40, 50];
  // An unusual saved goal stays selectable.
  const goals = choices.includes(settings.goal_sentences) ? choices : [...choices, settings.goal_sentences].sort((a, b) => a - b);
  return (
    <div className="settings-backdrop" onClick={onClose}>
      <div className="settings" onClick={(e) => e.stopPropagation()}>
        <h3>Settings</h3>
        <label>
          <span>Your level</span>
          <select value={settings.level} onChange={(e) => onSettings({ level: e.target.value })}>
            {settings.levels.map((l) => (
              <option key={l.id} value={l.id}>
                {l.id} · {l.label}
                {l.id === settings.suggested_level ? " (suggested)" : ""}
              </option>
            ))}
          </select>
          <small>How simply and slowly Sofía speaks. Takes full effect from the next lesson.</small>
        </label>
        <label>
          <span>Sofía's text</span>
          <select value={settings.subtitles} onChange={(e) => onSettings({ subtitles: e.target.value as Settings["subtitles"] })}>
            <option value="always">Always show</option>
            <option value="after">Show after she has spoken (listening practice)</option>
            <option value="tap">Hidden — tap to show (hard mode)</option>
          </select>
        </label>
        <label>
          <span>Daily goal</span>
          <select value={settings.goal_sentences} onChange={(e) => onSettings({ goal_sentences: Number(e.target.value) })}>
            {goals.map((n) => (
              <option key={n} value={n}>
                {n} sentences
              </option>
            ))}
          </select>
          <small>Every answer you say or type counts, and so do Repaso drills.</small>
        </label>
        <label>
          <span>Voice</span>
          <select value={voice} onChange={(e) => onVoice(e.target.value)}>
            {voices.map((v) => (
              <option key={v.id} value={v.id}>
                {v.label}
              </option>
            ))}
          </select>
        </label>
        <label className="check">
          <input type="checkbox" checked={sounds} onChange={(e) => onSounds(e.target.checked)} />
          <span>Sounds</span>
          <small>A soft chime when you use a new word.</small>
        </label>
        <button className="primary" onClick={onClose}>
          Done
        </button>
      </div>
    </div>
  );
}
