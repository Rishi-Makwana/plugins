import type { DatabaseSync } from 'node:sqlite'
import { identifierTerms } from './store.ts'
import type { ChatTurn } from './types.ts'
import { WINDOW_LINES } from './types.ts'

export const CHAT_PATH_PREFIX = 'chat/'

// Replaces any earlier chunks of the same (session, turn, role). Returns the number of chunks stored.
export function indexChatTurn(db: DatabaseSync, turn: ChatTurn): number {
  const path = `${CHAT_PATH_PREFIX}${turn.sessionId}`
  const lines = turn.text.split('\n')
  const windows: string[] = []
  if (turn.text.trim() !== '') {
    for (let i = 0; i < lines.length; i += WINDOW_LINES) windows.push(lines.slice(i, i + WINDOW_LINES).join('\n'))
  }

  db.exec('BEGIN')
  try {
    db.prepare("delete from chunks where kind = 'chat' and session_id = ? and start_line = ? and role = ?").run(
      turn.sessionId,
      turn.turnIndex,
      turn.role,
    )
    const insert = db.prepare(
      `insert into chunks (path, kind, start_line, end_line, text, session_id, role, created_at, terms)
       values (?, 'chat', ?, ?, ?, ?, ?, ?, ?)`,
    )
    for (const text of windows) {
      insert.run(path, turn.turnIndex, turn.turnIndex, text, turn.sessionId, turn.role, turn.createdAt, identifierTerms(text))
    }
    db.exec('COMMIT')
  } catch (error) {
    db.exec('ROLLBACK')
    throw error
  }
  return windows.length
}

export function forgetSession(db: DatabaseSync, sessionId: string): void {
  db.prepare("delete from chunks where kind = 'chat' and session_id = ?").run(sessionId)
}
