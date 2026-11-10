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

/** /view/:id (read-only) and /control/:id/:code: the mode depends only on the code being present */
export default function GamePage() {
  const { id = '', code } = useParams();
  const navigate = useNavigate();
  const dialog = useDialog();

  const onNotFound = useCallback(() => {
    navigate('/', { replace: true });
    void dialog.alert(`There is no game named "${id}".`, { title: 'Game not found' });
  }, [navigate, dialog, id]);

  const game = useGame(id, code, onNotFound);
  const { state, isViewMode } = game;
  const numbers = drawnNumbers(state);


  return (
    <div className="Bingo">
      <ConnectionStatus
        connection={game.connection}
        syncError={game.syncError}
        viewMode={isViewMode}
        onReconnect={game.reconnect}
      />
      <Board pickedNumbers={numbers} onNumberClick={game.drawNumber} viewMode={isViewMode} />
      <div className="info-sections">
        <LastNumbers numbers={numbers} />
        <div className="buttons" style={isViewMode ? { display: 'none' } : {}}>
          <button onClick={game.undo} disabled={state.eventPosition <= 0}>
            <img alt="Undo" src={undoicon} />
          </button>
          <button onClick={game.redo} disabled={state.eventPosition >= state.eventHistory.length}>
            <img alt="Redo" src={redoicon} />
          </button>
          <button onClick={game.reset}>
            <img alt="Reset" src={reseticon} />
          </button>
        </div>
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
            <button onClick={() => game.setBingo(true)} disabled={state.eventPosition <= 0} className="bingoButton">
              Bingo
            </button>
          </div>
        ) : null}
      </div>
    </div>
  );
}
