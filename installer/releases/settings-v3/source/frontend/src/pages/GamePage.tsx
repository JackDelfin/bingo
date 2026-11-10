import { useCallback } from 'react';
import { useNavigate, useParams } from 'react-router-dom';
import redoicon from '../assets/redo.svg';
import reseticon from '../assets/reset.svg';
import undoicon from '../assets/undo.svg';
import Board from '../components/Board';

import ConnectionStatus from '../components/ConnectionStatus';
import LastNumbers from '../components/LastNumbers';
import WinningPattern from '../components/WinningPattern';
import { useDialog } from '../dialog/DialogContext';
import { drawnNumbers } from '../game/history';
import { useGame } from '../game/useGame';
import { useNumberCallouts } from '../game/useNumberCallouts';
import SettingsPanel from '../settings/SettingsPanel';
import { usePreferences } from '../settings/usePreferences';
import UpdatesPanel from '../updates/UpdatesPanel';

/** /view/:id (read-only) and /control/:id/:code: the mode depends only on the code being present */
export default function GamePage() {
  const { id = '', code } = useParams();
  return <GameSession key={JSON.stringify([id, code])} id={id} code={code} />;
}

/** Route changes end the old controller's audio and start with audio off. */
function GameSession({ id, code }: { id: string; code: string | undefined }) {
  const navigate = useNavigate();
  const dialog = useDialog();

  const onNotFound = useCallback(() => {
    navigate('/', { replace: true });
    void dialog.alert(`There is no game named "${id}".`, { title: 'Game not found' });
  }, [navigate, dialog, id]);

  const game = useGame(id, code, onNotFound);
  const { state, isViewMode } = game;
  const preferences = usePreferences();
  const numbers = drawnNumbers(state);
  const callouts = useNumberCallouts(numbers, state.bingo, preferences.volume / 100);
  const drawNumber = (number: number) => {
    if (isViewMode || state.bingo || numbers.includes(number)) {
      game.drawNumber(number);
      return;
    }
    game.drawNumber(number);
    callouts.announce(number);
  };

  return (
    <div className="Bingo">
      <ConnectionStatus
        connection={game.connection}
        syncError={game.syncError}
        viewMode={isViewMode}
        onReconnect={game.reconnect}
      />
      <Board pickedNumbers={numbers} onNumberClick={drawNumber} viewMode={isViewMode} />
      <div className="info-sections">
        <LastNumbers numbers={numbers} />
        <div className="buttons" style={isViewMode ? { display: 'none' } : {}}>
          <button onClick={() => { callouts.stop(); game.undo(); }} disabled={state.eventPosition <= 0}>
            <img alt="Undo" src={undoicon} />
          </button>
          <button onClick={() => { callouts.stop(); game.redo(); }} disabled={state.eventPosition >= state.eventHistory.length}>
            <img alt="Redo" src={redoicon} />
          </button>
          <button onClick={() => { callouts.stop(); game.reset(); }}>
            <img alt="Reset" src={reseticon} />
          </button>
        </div>
        {!isViewMode && (
          <div className="audio-callouts">
            <SettingsPanel preferences={preferences} audio={callouts}><UpdatesPanel /></SettingsPanel>
            <label>
              <input type="checkbox" checked={callouts.enabled} aria-describedby="audio-callouts-help" onChange={(event) => callouts.setEnabled(event.target.checked)} />
              Audio callouts
            </label>
            <small id="audio-callouts-help">Plays on this device only. Works offline.</small>
            {callouts.error && <p role="alert">{callouts.error}</p>}
          </div>
        )}
      </div>
      <div className="bingo-validation">
        {state.bingo && (isViewMode ? <div className="manual-bingo-message">BINGO!<small>Checking a claim</small></div> : (
          <div className="manual-check">
            <h2>Check the physical card</h2>
            <p>Match “{state.selectedPattern.name}” against the called balls. This display does not validate your paper cards.</p>
            <button onClick={() => game.awardPatterns([state.selectedPattern.id], false)}>Confirm winner</button>
            <button onClick={() => game.awardPatterns([state.selectedPattern.id], true)}>Confirm &amp; check another</button>
            <button onClick={() => game.setBingo(false)}>Cancel check</button>
          </div>
        ))}
      </div>
      <div className="bingo-status">
        <WinningPattern selection={state.selectedPattern} readOnly={isViewMode} disabled={state.bingo} onChange={game.selectPattern} />
        {!state.bingo && !isViewMode ? (
          <div>
            <button onClick={() => { callouts.stop(); game.setBingo(true); }} disabled={state.eventPosition <= 0} className="bingoButton">
              Bingo
            </button>
          </div>
        ) : null}
      </div>
    </div>
  );
}
