"use strict";
var __awaiter = (this && this.__awaiter) || function (thisArg, _arguments, P, generator) {
    function adopt(value) { return value instanceof P ? value : new P(function (resolve) { resolve(value); }); }
    return new (P || (P = Promise))(function (resolve, reject) {
        function fulfilled(value) { try { step(generator.next(value)); } catch (e) { reject(e); } }
        function rejected(value) { try { step(generator["throw"](value)); } catch (e) { reject(e); } }
        function step(result) { result.done ? resolve(result.value) : adopt(result.value).then(fulfilled, rejected); }
        step((generator = generator.apply(thisArg, _arguments || [])).next());
    });
};
var _a, _b;
Object.defineProperty(exports, "__esModule", { value: true });
const express = require("express");
const http = require("http");
const WebSocket = require("ws");
const randtoken = require("rand-token");
const db_1 = require("./db");
const bingo_model_1 = require("./bingo-model");
const validation_1 = require("./validation");
const cors = require("cors");
const app = express();
//In production nginx serves the frontend and proxies /api on the same origin, so CORS is only needed
//when the CRA dev server (another port) calls the backend directly
if (process.env.NODE_ENV !== 'production') {
    app.use(cors({ origin: (_b = (_a = process.env.CORS_ORIGIN) === null || _a === void 0 ? void 0 : _a.split(',')) !== null && _b !== void 0 ? _b : ['http://localhost:3000', 'http://127.0.0.1:3000'] }));
}
db_1.default.on('error', console.error.bind(console, 'MongoDB connection error:'));
//initialize a simple http server
const server = http.createServer(app);
//initialize the WebSocket server instance
const wss = new WebSocket.Server({ server });
const validator = new validation_1.default('cards.csv');
let getCurrentState = (gameId) => __awaiter(void 0, void 0, void 0, function* () {
    return yield bingo_model_1.default.findOne({ gameId: gameId }).then(game => {
        return game;
    }).catch(() => {
        return null;
    });
});
//Games not updated for this long are hidden from the list and deleted on the next game creation
const STALE_GAME_AGE_MS = 1000 * 86400 * 2;
let staleCutoff = () => new Date(Date.now() - STALE_GAME_AGE_MS);
app.use(express.json());
//Edit types here!
//Array of Arrays of strings: each array is displayed on a different line (max 2 lines)
app.get('/types', (req, res) => __awaiter(void 0, void 0, void 0, function* () {
    //return res.status(200).json([['b', 'i', 'n', 'g', 'o'], ['row', 'diag', 'corners', 'x', 'full']]);
    return res.status(200).json([['row', 'column', 'diag', 'corners']]);
    //return res.status(200).json([['full']]);
}));
app.post('/validate', (req, res) => __awaiter(void 0, void 0, void 0, function* () {
    return res.status(200).json(validator.validate(parseInt(req.body.cardNumber), req.body.numbers, req.body.patterns));
}));
app.post('/create', (req, res) => __awaiter(void 0, void 0, void 0, function* () {
    //Cleanup failure must not prevent creating the game
    yield bingo_model_1.default.deleteMany({ updatedAt: { $lt: staleCutoff() } }).catch(err => {
        console.error('Failed to delete stale games:', err);
    });
    let code = randtoken.generate(12, "abcdefghijklnmopqrstuvwxyz0123456789");
    let schema = new bingo_model_1.default({ gameId: req.body.gameId, code: code, isPublic: req.body.isPublic });
    if (!schema) {
        return res.status(200).json({ success: false, error: "Schema invalid" });
    }
    schema.save().then(() => {
        return res.status(200).json({ success: true, code: code });
    }).catch((error) => {
        return res.status(200).json({ success: false, error: error });
    });
}));
app.get('/list', (req, res) => __awaiter(void 0, void 0, void 0, function* () {
    bingo_model_1.default.find({ isPublic: true, updatedAt: { $gte: staleCutoff() } }, 'gameId').then(games => {
        return res.status(200).json({ games: games.map(g => g.get('gameId')) });
    }).catch(err => {
        return res.status(400).json({ games: [], error: err });
    });
}));
let send = (ws, msg) => {
    if (ws.readyState === WebSocket.OPEN)
        ws.send(JSON.stringify(msg));
};
let handleRegister = (ws, msg) => __awaiter(void 0, void 0, void 0, function* () {
    ws.gameId = String(msg.gameId);
    const game = yield getCurrentState(ws.gameId);
    if (!game)
        return send(ws, { type: 'notFound' });
    send(ws, { type: 'state', state: (0, bingo_model_1.publicState)(game) });
});
let handlePush = (ws, msg) => __awaiter(void 0, void 0, void 0, function* () {
    const gameId = String(msg.gameId);
    const code = String(msg.code);
    const baseRevision = Number(msg.baseRevision);
    if (!Number.isInteger(baseRevision) || typeof msg.pushId !== 'string' || typeof msg.state !== 'object' || msg.state === null) {
        return send(ws, { type: 'error', error: 'badRequest', pushId: msg.pushId });
    }
    let update;
    try {
        // Validate explicit selections before any write; old clients leave the saved choice alone.
        update = (0, bingo_model_1.buildStateUpdate)(msg.state, baseRevision, msg.pushId);
    }
    catch (_a) {
        return send(ws, { type: 'error', error: 'badRequest', pushId: msg.pushId });
    }
    //Games created before revisions existed have no revision field
    const revisionFilter = baseRevision === 0 ? { $or: [{ revision: 0 }, { revision: { $exists: false } }] } : { revision: baseRevision };
    const game = yield bingo_model_1.default.findOneAndUpdate(Object.assign({ gameId, code }, revisionFilter), { $set: update }, { runValidators: true, returnDocument: 'after' });
    if (!game) {
        const current = yield getCurrentState(gameId);
        if (!current)
            return send(ws, { type: 'notFound' });
        if (current.get('code') !== code)
            return send(ws, { type: 'error', error: 'unauthorized', pushId: msg.pushId });
        //Someone else pushed since this client's base revision
        return send(ws, { type: 'conflict', pushId: msg.pushId, state: (0, bingo_model_1.publicState)(current) });
    }
    const state = (0, bingo_model_1.publicState)(game);
    send(ws, { type: 'ack', pushId: msg.pushId, revision: state.revision });
    const broadcast = JSON.stringify({ type: 'state', state });
    wss.clients.forEach(client => {
        if (client !== ws && client.readyState === WebSocket.OPEN && client.gameId === gameId) {
            client.send(broadcast);
        }
    });
});
wss.on('connection', (ws) => {
    ws.isAlive = true;
    ws.on('pong', () => { ws.isAlive = true; });
    ws.on('message', (data) => __awaiter(void 0, void 0, void 0, function* () {
        let msg;
        try {
            msg = JSON.parse(data.toString());
        }
        catch (_a) {
            return send(ws, { type: 'error', error: 'badRequest' });
        }
        try {
            switch (msg === null || msg === void 0 ? void 0 : msg.action) {
                case 'register':
                    yield handleRegister(ws, msg);
                    break;
                case 'push':
                    yield handlePush(ws, msg);
                    break;
                case 'ping':
                    send(ws, { type: 'pong' });
                    break;
                default:
                    send(ws, { type: 'error', error: 'unknownAction' });
            }
        }
        catch (err) {
            console.error(err);
            send(ws, { type: 'error', error: 'serverError', pushId: msg === null || msg === void 0 ? void 0 : msg.pushId });
        }
    }));
});
//Terminate connections that stopped answering pings (sleeping phones, dropped networks)
const heartbeat = setInterval(() => {
    wss.clients.forEach(client => {
        const ws = client;
        if (!ws.isAlive)
            return ws.terminate();
        ws.isAlive = false;
        ws.ping();
    });
}, 30000);
wss.on('close', () => clearInterval(heartbeat));
//start our server
server.listen(process.env.PORT || 8999, () => {
    const { port } = server.address();
    console.log(`Server started on port ${port} :)`);
});
//Close sockets on docker stop so clients see the disconnect immediately instead of after the kill timeout
let shutdown = () => {
    console.log('Shutting down');
    wss.clients.forEach(client => client.close(1001, 'Server shutting down'));
    wss.close();
    server.close(() => db_1.default.close().finally(() => process.exit(0)));
    setTimeout(() => process.exit(0), 3000).unref();
};
process.on('SIGTERM', shutdown);
process.on('SIGINT', shutdown);
//# sourceMappingURL=server.js.map