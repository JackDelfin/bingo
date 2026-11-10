"use strict";
Object.defineProperty(exports, "__esModule", { value: true });
const mongoose = require("mongoose");
const mongoHost = process.env.NODE_ENV === 'production' ? 'db' : 'localhost';
mongoose
    .connect(`mongodb://${mongoHost}:27017/bingo`)
    .catch((e) => {
    console.error('Connection error', e.message);
});
const db = mongoose.connection;
exports.default = db;
//# sourceMappingURL=db.js.map