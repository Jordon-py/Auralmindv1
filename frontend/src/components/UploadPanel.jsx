import React, { useMemo, useState } from 'react';

const PRESETS = [
  { id: 'hi_fi_streaming', name: 'Hi-Fi Streaming', description: 'Neutral translation with harmonic interval clarity.' },
  { id: 'radio_loud', name: 'Radio Loud', description: 'Presence-forward vocal contour with controlled punch.' },
  { id: 'cinematic', name: 'Wide Cinematic', description: 'Expanded depth and motion for orchestral or score energy.' },
  { id: 'club', name: 'Club Impact', description: '808 phase-anchored low end with groove-locked transient drive.' },
];

export default function UploadPanel({ onSubmit, loading }) {
  const [target, setTarget] = useState(null);
  const [reference, setReference] = useState(null);
  const [preset, setPreset] = useState('hi_fi_streaming');
  const [monoSub, setMonoSub] = useState(true);
  const [dynamicEq, setDynamicEq] = useState(true);
  const [truepeakLimiter, setTruepeakLimiter] = useState(true);
  const [warmth, setWarmth] = useState(0);
  const [targetLufs, setTargetLufs] = useState('');
  const [truePeakCeiling, setTruePeakCeiling] = useState('-1.0');
  const [sectionAwareMastering, setSectionAwareMastering] = useState(false);
  const [sectionLiftMix, setSectionLiftMix] = useState('0.2');
  const [grooveTransientSculpting, setGrooveTransientSculpting] = useState(false);
  const [grooveTransientBoostDb, setGrooveTransientBoostDb] = useState('1.8');
  const [outputPcmBits, setOutputPcmBits] = useState('16');
  const [openAdvanced, setOpenAdvanced] = useState(false);
  const [formError, setFormError] = useState(null);
  const [dragActive, setDragActive] = useState(false);

  const selectedTargetName = useMemo(() => target?.name || 'Drop target audio here or click to browse', [target]);

  const onDropTarget = (e) => {
    e.preventDefault();
    setDragActive(false);
    const file = e.dataTransfer.files?.[0];
    if (file) setTarget(file);
  };

  const handleSubmit = (e) => {
    e.preventDefault();
    setFormError(null);
    if (!target) {
      setFormError('Please choose a target file');
      return;
    }

    const formData = new FormData();
    formData.append('target', target);
    if (reference) formData.append('reference', reference);

    const settings = {
      preset,
      mono_sub: monoSub,
      dynamic_eq: dynamicEq,
      truepeak_limiter: truepeakLimiter,
      warmth: parseFloat(warmth),
      target_lufs: targetLufs ? parseFloat(targetLufs) : undefined,
      true_peak_ceiling: truePeakCeiling ? parseFloat(truePeakCeiling) : -1.0,
      section_aware_mastering: sectionAwareMastering,
      section_lift_mix: sectionAwareMastering ? parseFloat(sectionLiftMix) : undefined,
      groove_transient_sculpting: grooveTransientSculpting,
      groove_transient_boost_db: grooveTransientSculpting ? parseFloat(grooveTransientBoostDb) : undefined,
      output_pcm_bits: parseInt(outputPcmBits, 10),
    };
    onSubmit(formData, settings);
  };

  return (
    <form className="panel" onSubmit={handleSubmit}>
      <fieldset disabled={loading}>
        <legend>Upload & Mastering Settings</legend>

        <div
          className={`dropzone ${dragActive ? 'is-dragging' : ''}`}
          onDragOver={(e) => {
            e.preventDefault();
            setDragActive(true);
          }}
          onDragLeave={() => setDragActive(false)}
          onDrop={onDropTarget}
        >
          <label htmlFor="target-input" className="dropzone-label">{selectedTargetName}</label>
          <div className="file-input-wrapper">
            <input
              id="target-input"
              type="file"
              accept=".wav,.mp3,.aiff,.flac,.ogg"
              required
              onChange={(e) => setTarget(e.target.files?.[0] || null)}
            />
            {target && <span className="filename">{target.name}</span>}
          </div>
        </div>

        <div className="field">
          <label htmlFor="reference-input">Reference track (optional)</label>
          <div className="file-input-wrapper">
            <input
              id="reference-input"
              type="file"
              accept=".wav,.mp3,.aiff,.flac,.ogg"
              onChange={(e) => setReference(e.target.files?.[0] || null)}
            />
            {reference && <span className="filename">{reference.name}</span>}
          </div>
        </div>

        <div className="preset-grid" role="radiogroup" aria-label="Mastering presets">
          {PRESETS.map((item) => (
            <button
              key={item.id}
              type="button"
              className={`preset-card ${preset === item.id ? 'is-active' : ''}`}
              onClick={() => setPreset(item.id)}
            >
              <strong>{item.name}</strong>
              <span>{item.description}</span>
            </button>
          ))}
        </div>

        <details className="advanced" open={openAdvanced} onToggle={(e) => setOpenAdvanced(e.currentTarget.open)}>
          <summary>Advanced modules (defaults follow the preset)</summary>
          <div className="field checkbox-group">
            <label>
              <input type="checkbox" checked={monoSub} onChange={(e) => setMonoSub(e.target.checked)} />
              Sub-band mono + phase anchor
            </label>
            <label>
              <input type="checkbox" checked={dynamicEq} onChange={(e) => setDynamicEq(e.target.checked)} />
              Masking-aware dynamic EQ (200–500Hz)
            </label>
            <label>
              <input type="checkbox" checked={truepeakLimiter} onChange={(e) => setTruepeakLimiter(e.target.checked)} />
              True-peak safe limiter + soft clip
            </label>
          </div>
          <div className="field">
            <label htmlFor="warmth-range">Analog Warmth: {warmth}%</label>
            <input
              id="warmth-range"
              type="range"
              min="0"
              max="100"
              step="1"
              value={warmth}
              onChange={(e) => setWarmth(e.target.value)}
            />
          </div>
          <div className="field field-grid">
            <div>
              <label htmlFor="target-lufs">Target LUFS (optional)</label>
              <input id="target-lufs" type="number" step="0.1" value={targetLufs} onChange={(e) => setTargetLufs(e.target.value)} placeholder="-11 to -9" />
            </div>
            <div>
              <label htmlFor="truepeak">True Peak Ceiling (dBTP)</label>
              <input id="truepeak" type="number" step="0.1" value={truePeakCeiling} onChange={(e) => setTruePeakCeiling(e.target.value)} />
            </div>
          </div>

          <div className="field checkbox-group">
            <label>
              <input type="checkbox" checked={sectionAwareMastering} onChange={(e) => setSectionAwareMastering(e.target.checked)} />
              Section-aware mastering (hook lift, guard-railed)
            </label>
            <label>
              <input type="checkbox" checked={grooveTransientSculpting} onChange={(e) => setGrooveTransientSculpting(e.target.checked)} />
              Groove-locked transient sculpting
            </label>
          </div>
          <div className="field field-grid">
            <div>
              <label htmlFor="section-lift-mix">Section lift mix (0.00–0.65)</label>
              <input
                id="section-lift-mix"
                type="number"
                min="0"
                max="0.65"
                step="0.01"
                value={sectionLiftMix}
                onChange={(e) => setSectionLiftMix(e.target.value)}
                disabled={!sectionAwareMastering}
              />
            </div>
            <div>
              <label htmlFor="groove-boost-db">Transient boost cap (dB)</label>
              <input
                id="groove-boost-db"
                type="number"
                min="0"
                max="3.5"
                step="0.1"
                value={grooveTransientBoostDb}
                onChange={(e) => setGrooveTransientBoostDb(e.target.value)}
                disabled={!grooveTransientSculpting}
              />
            </div>
          </div>
          <div className="field">
            <label htmlFor="output-pcm-bits">WAV output bit depth (device compatibility)</label>
            <select id="output-pcm-bits" value={outputPcmBits} onChange={(e) => setOutputPcmBits(e.target.value)}>
              <option value="16">16-bit PCM (recommended, widest compatibility)</option>
              <option value="24">24-bit PCM (higher resolution)</option>
            </select>
          </div>
        </details>

        {formError && <p className="error" role="alert">{formError}</p>}
        <button type="submit" disabled={loading}>{loading ? 'Uploading…' : 'Start Mastering'}</button>
      </fieldset>
    </form>
  );
}
