// @ts-check
/** Shared app state. */
export const state = {
  /** @type {any} signed-in user from /api/me */
  me: null,
  /** request typed on Home, carried into the new-session dialog */
  pendingRequest: "",
  /** @type {{sessionId: number, text: string} | null} first message to send once a new session opens */
  firstMessage: null,
};
