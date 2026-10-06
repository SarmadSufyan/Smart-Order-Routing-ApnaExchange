import { create } from 'zustand'
import { copilotApi, type CopilotStatus, type Proposal } from '../services/copilotApi'

/**
 * Order copilot chat state ("Ask ApnaSOR").
 *
 * The trader types an order in plain English. The backend turns it into a
 * proposal; NOTHING is sent to an exchange until the trader presses Confirm on
 * that proposal. Confirming goes through the normal order path (pre-trade risk,
 * kill switch, funding gate), so this store never bypasses a safety check.
 *
 * Lives in its own store so the conversation survives navigation between pages.
 */

export interface ChatMessage {
  id: string
  role: 'user' | 'assistant'
  content: string
  error?: boolean
  at: number
  /** Present on assistant messages that carry a proposal card. */
  proposal?: Proposal
  /** True while confirm/cancel for this card is in flight. */
  busy?: boolean
  /** Problem from the last confirm/cancel attempt (shown under the card). */
  actionError?: string
}

interface AssistantState {
  open: boolean
  loading: boolean
  messages: ChatMessage[]
  status: CopilotStatus | null
  setOpen: (open: boolean) => void
  toggle: () => void
  loadStatus: () => Promise<void>
  send: (text: string) => Promise<void>
  confirm: (messageId: string) => Promise<void>
  cancel: (messageId: string) => Promise<void>
  clear: () => void
}

let counter = 0
function newId(): string {
  counter += 1
  return `m${Date.now().toString(36)}${counter}`
}

function errText(err: unknown): string {
  return err instanceof Error && err.message ? err.message : 'Unknown error'
}

export const useAssistantStore = create<AssistantState>((set, get) => {
  const patch = (id: string, p: Partial<ChatMessage>) =>
    set((s) => ({ messages: s.messages.map((m) => (m.id === id ? { ...m, ...p } : m)) }))

  const act = async (id: string, kind: 'confirm' | 'cancel') => {
    const msg = get().messages.find((m) => m.id === id)
    const proposal = msg?.proposal
    // One shot: ignore if there is nothing to act on or a request is already running.
    if (!msg || !proposal || msg.busy || proposal.status !== 'PROPOSED') return
    patch(id, { busy: true, actionError: undefined })
    try {
      const updated = kind === 'confirm'
        ? await copilotApi.confirm(proposal.id)
        : await copilotApi.cancel(proposal.id)
      patch(id, { proposal: updated, content: updated.message || msg.content, busy: false })
    } catch (err) {
      patch(id, { busy: false, actionError: errText(err) })
    }
  }

  return {
    open: false,
    loading: false,
    messages: [],
    status: null,

    setOpen: (open) => set({ open }),
    toggle: () => set((s) => ({ open: !s.open })),

    loadStatus: async () => {
      try {
        set({ status: await copilotApi.status() })
      } catch (err) {
        set({ status: { available: false, provider: '', model: null, reason: errText(err) } })
      }
    },

    send: async (text) => {
      const trimmed = text.trim()
      if (!trimmed || get().loading) return

      const userMsg: ChatMessage = { id: newId(), role: 'user', content: trimmed, at: Date.now() }
      set((s) => ({ messages: [...s.messages, userMsg], loading: true }))

      try {
        const proposal = await copilotApi.propose(trimmed)
        const botMsg: ChatMessage = {
          id: newId(), role: 'assistant', content: proposal.message, at: Date.now(), proposal,
        }
        set((s) => ({ messages: [...s.messages, botMsg], loading: false }))
      } catch (err) {
        // No made-up answers: show the real problem.
        const errMsg: ChatMessage = {
          id: newId(), role: 'assistant', error: true, at: Date.now(),
          content: `I couldn't read that request: ${errText(err)}`,
        }
        set((s) => ({ messages: [...s.messages, errMsg], loading: false }))
      }
    },

    confirm: (id) => act(id, 'confirm'),
    cancel: (id) => act(id, 'cancel'),

    clear: () => set({ messages: [] }),
  }
})
