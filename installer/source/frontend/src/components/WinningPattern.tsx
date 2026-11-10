import * as AlertDialog from '@radix-ui/react-alert-dialog';
import { useEffect, useState } from 'react';
import type { PatternSelection } from '../game/types';
import { PATTERNS } from '../patterns/catalog';
import './winning-pattern.css';

const LETTERS = ['B', 'I', 'N', 'G', 'O'];
const EMPTY = '0000000000001000000000000';
const required = (mask: string) => [...mask].some((value, index) => index !== 12 && value === '1');
const rotate = (mask: string) => Array.from({ length: 25 }, (_, i) => mask[(4 - (i % 5)) * 5 + Math.floor(i / 5)]).join('');

function PatternDiagram({ mask }: { mask: string }) {
  return (
    <svg viewBox="0 0 110 110" role="img" aria-label="Winning pattern diagram; star is the free center" className="pattern-diagram" data-mask={mask}>
      {Array.from({ length: 25 }, (_, index) => (
        <g key={index} data-cell={index} data-required={mask[index] === '1'}>
          <rect x={(index % 5) * 22 + 1} y={Math.floor(index / 5) * 22 + 1} width="19" height="19" rx="2" fill={mask[index] === '1' ? '#f5f5f5' : '#151515'} stroke="#666" />
          {index === 12 && <text x="54.5" y="60" textAnchor="middle" fontSize="15" fill={mask[index] === '1' ? '#000' : '#aaa'}>★</text>}
        </g>
      ))}
    </svg>
  );
}

function CustomEditor({ selection, onSave, onClose }: { selection: PatternSelection; onSave: (selection: PatternSelection) => void; onClose: () => void }) {
  const [name, setName] = useState(selection.id === 'custom' ? selection.name : 'Custom pattern');
  const [variants, setVariants] = useState(selection.id === 'custom' ? [...selection.variants] : [selection.variants[0]]);
  const [index, setIndex] = useState(0);
  const update = (mask: string) => setVariants((old) => old.map((item, i) => i === index ? mask : item));
  const valid = name.trim().length > 0 && name.trim().length <= 60 && variants.every(required);
  const mask = variants[index];
  const save = () => {
    if (!valid) return;
    onSave({ id: 'custom', name: name.trim(), variants: [...new Set(variants)] });
    onClose();
  };
  return (
    <AlertDialog.Root open onOpenChange={(open) => { if (!open) onClose(); }}>
      <AlertDialog.Portal>
        <AlertDialog.Overlay className="dialog-overlay" />
        <AlertDialog.Content className="dialog-content pattern-editor">
          <AlertDialog.Title className="dialog-title">Custom winning pattern</AlertDialog.Title>
          <AlertDialog.Description>Tap the required squares. The center is always free. Any one alternative wins.</AlertDialog.Description>
          <label htmlFor="custom-pattern-name">Pattern name</label>
          <input id="custom-pattern-name" value={name} maxLength={60} onChange={(e) => setName(e.target.value)} />
          <div className="custom-pattern-grid">
            {Array.from({ length: 25 }, (_, cell) => (
              <button key={cell} type="button" disabled={cell === 12} aria-label={cell === 12 ? 'FREE center' : `Row ${Math.floor(cell / 5) + 1}, ${LETTERS[cell % 5]}`} aria-pressed={mask[cell] === '1'} onClick={() => update([...mask].map((value, i) => i === cell ? (value === '1' ? '0' : '1') : value).join(''))}>
                {cell === 12 ? '★' : mask[cell] === '1' ? '●' : '·'}
              </button>
            ))}
          </div>
          <div className="pattern-editor-tools">
            <button type="button" onClick={() => update(EMPTY)}>Clear squares</button>
            <button type="button" onClick={() => update('1'.repeat(25))}>Fill squares</button>
            <button type="button" onClick={() => update(rotate(mask))}>Rotate clockwise</button>
          </div>
          <p>Alternative {index + 1} of {variants.length}</p>
          <div className="pattern-editor-tools">
            <button type="button" disabled={variants.length < 2} onClick={() => setIndex((index + variants.length - 1) % variants.length)}>Previous alternative</button>
            <button type="button" disabled={variants.length < 2} onClick={() => setIndex((index + 1) % variants.length)}>Next alternative</button>
            <button type="button" disabled={variants.length >= 12} onClick={() => { setVariants([...variants, mask]); setIndex(variants.length); }}>Add alternative</button>
            <button type="button" disabled={variants.length < 2} onClick={() => { setVariants(variants.filter((_item, i) => i !== index)); setIndex(Math.max(0, index - 1)); }}>Remove alternative</button>
          </div>
          <div className="dialog-buttons">
            <AlertDialog.Cancel className="dialog-button">Cancel</AlertDialog.Cancel>
            <button type="button" className="dialog-button dialog-button-primary" disabled={!valid} onClick={save}>Save pattern</button>
          </div>
        </AlertDialog.Content>
      </AlertDialog.Portal>
    </AlertDialog.Root>
  );
}

interface Props {
  selection: PatternSelection;
  readOnly: boolean;
  disabled: boolean;
  onChange: (selection: PatternSelection) => void;
}

/** A caller-only chooser and the same explicit winning shape on every audience screen. */
export default function WinningPattern(props: Props) {
  // Keying the display resets example/editor state on a remote pattern change.
  return <WinningPatternDisplay key={JSON.stringify(props.selection)} {...props} />;
}

function WinningPatternDisplay({ selection, readOnly, disabled, onChange }: Props) {
  const [example, setExample] = useState(0);
  const [editing, setEditing] = useState(false);
  useEffect(() => {
    if (!readOnly || selection.variants.length < 2 || window.matchMedia?.('(prefers-reduced-motion: reduce)').matches) return;
    const timer = setInterval(() => setExample((value) => (value + 1) % selection.variants.length), 3500);
    return () => clearInterval(timer);
  }, [readOnly, selection.variants.length]);
  const info = PATTERNS.find((pattern) => pattern.id === selection.id);
  const categories = [...new Set(PATTERNS.map((pattern) => pattern.category))];
  const choose = (id: string) => {
    if (disabled || readOnly) return;
    if (id === 'custom') { setEditing(true); return; }
    const preset = PATTERNS.find((pattern) => pattern.id === id);
    if (preset) onChange({ id: preset.id, name: preset.name, variants: [...preset.variants] });
  };
  return (
    <section className="winning-pattern" aria-label="Current winning pattern">
      <div className="pattern-kicker">PLAYING FOR</div>
      {readOnly ? <h2 className="pattern-name">{selection.name}</h2> : (
        <>
          <label className="sr-only" htmlFor="winning-pattern">Winning pattern</label>
          <select id="winning-pattern" value={selection.id} disabled={disabled} onChange={(e) => choose(e.target.value)}>
            {!PATTERNS.some((pattern) => pattern.id === selection.id) && selection.id !== 'custom' && <option value={selection.id}>{selection.name}</option>}
            {categories.map((category) => (
              <optgroup label={category} key={category}>
                {PATTERNS.filter((pattern) => pattern.category === category).map((pattern) => <option value={pattern.id} key={pattern.id}>{pattern.name}</option>)}
              </optgroup>
            ))}
            <option value="custom">{selection.id === 'custom' ? selection.name : 'Custom pattern…'}</option>
          </select>
          {selection.id === 'custom' && <button className="pattern-edit-button" disabled={disabled} onClick={() => setEditing(true)}>Edit custom pattern</button>}
        </>
      )}
      <div className="pattern-visual">
        <PatternDiagram mask={selection.variants[example]} />
        <div className="pattern-explanation">
          <p>{selection.id === 'custom' ? 'House pattern — match the highlighted squares.' : info?.description ?? 'Match the highlighted squares.'}</p>
          {selection.variants.length > 1 ? (
            <>
              <p className="pattern-alternatives">Any one of {selection.variants.length} alternatives wins.</p>
              <div className="pattern-example">
                <span>Example {example + 1} / {selection.variants.length}</span>
                {!readOnly && <button type="button" aria-label="Next example" onClick={() => setExample((example + 1) % selection.variants.length)}>Next ›</button>}
              </div>
            </>
          ) : <p className="pattern-alternatives">Exact shape shown.</p>}
          <p className="pattern-free">★ Free center · All called balls count</p>
        </div>
      </div>
      {editing && !readOnly && !disabled && <CustomEditor selection={selection} onSave={onChange} onClose={() => setEditing(false)} />}
    </section>
  );
}
