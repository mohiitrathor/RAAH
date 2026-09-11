/**
 * RAAH Authoritative Workspace Navigation Authority
 * =================================================
 * Single source of truth for switching workspaces.
 *
 * Strict Rules:
 * - Does NOT set presentation-specific inline display values (no grid/flex via JS).
 * - Uses semantic state class .workspace--active on the workspace container.
 * - Preserves all existing workspace IDs and controller hooks.
 */

class WorkspaceNavigationService {
  constructor() {
    this.activeWorkspaceId = 'tactical';
    this.workspaces = new Map();
    this.hooks = new Map();
    this.initialized = false;
  }

  /**
   * Register a workspace with its button ID and container element ID.
   */
  register(id, { buttonId, containerId, onActivate = null, onDeactivate = null }) {
    const btn = document.getElementById(buttonId);
    const container = document.getElementById(containerId);

    this.workspaces.set(id, {
      id,
      buttonId,
      containerId,
      button: btn,
      container: container,
    });

    if (onActivate || onDeactivate) {
      this.hooks.set(id, { onActivate, onDeactivate });
    }

    if (btn) {
      btn.addEventListener('click', (e) => {
        e.preventDefault();
        this.switchTo(id);
      });
    }
  }

  /**
   * Register a lifecycle callback for a workspace.
   */
  on(workspaceId, eventType, callback) {
    if (!this.hooks.has(workspaceId)) {
      this.hooks.set(workspaceId, {});
    }
    const h = this.hooks.get(workspaceId);
    if (eventType === 'activate') {
      h.onActivate = callback;
    } else if (eventType === 'deactivate') {
      h.onDeactivate = callback;
    }
  }

  /**
   * Switch active workspace using semantic classes.
   */
  switchTo(workspaceId) {
    if (!this.workspaces.has(workspaceId)) {
      console.warn('[Navigation] Unknown workspace:', workspaceId);
      return;
    }

    const prevWorkspaceId = this.activeWorkspaceId;
    if (prevWorkspaceId === workspaceId && this.initialized) {
      const currentHooks = this.hooks.get(workspaceId);
      if (currentHooks?.onActivate) {
        currentHooks.onActivate();
      }
      return;
    }

    const prevHooks = this.hooks.get(prevWorkspaceId);
    if (prevHooks?.onDeactivate) {
      try {
        prevHooks.onDeactivate();
      } catch (err) {
        console.error('[Navigation] Error during deactivate:', err);
      }
    }

    this.activeWorkspaceId = workspaceId;

    // Update DOM classes strictly semantically
    for (const [id, ws] of this.workspaces.entries()) {
      const isActive = (id === workspaceId);
      const btn = ws.button || document.getElementById(ws.buttonId);
      const container = ws.container || document.getElementById(ws.containerId);

      if (btn) {
        btn.classList.toggle('active', isActive);
        btn.setAttribute('aria-selected', isActive ? 'true' : 'false');
      }
      if (container) {
        container.classList.toggle('workspace--active', isActive);
        // Clear any lingering inline display style to let CSS class govern
        if (container.style.display) {
          container.style.display = '';
        }
      }
    }

    const newHooks = this.hooks.get(workspaceId);
    if (newHooks?.onActivate) {
      try {
        newHooks.onActivate();
      } catch (err) {
        console.error('[Navigation] Error during activate:', err);
      }
    }
  }

  init(defaultWorkspace = 'tactical') {
    this.initialized = true;
    this.switchTo(defaultWorkspace);
  }

  getActiveWorkspace() {
    return this.activeWorkspaceId;
  }
}

export const navigation = new WorkspaceNavigationService();
