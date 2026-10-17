export interface Message {
  role: 'user' | 'assistant'
  content: string
}

export function appendMessage(history: Message[], message: Message): Message[] {
  return [...history, message]
}

export function lastAssistantReply(history: Message[]): string | null {
  for (let i = history.length - 1; i >= 0; i--) {
    if (history[i]!.role === 'assistant') return history[i]!.content
  }
  return null
}
