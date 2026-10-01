<script lang="ts">
  import { onMount } from 'svelte';

  let { initialText, onApply, onClose }: {
    initialText: string;
    onApply: (text: string) => void;
    onClose: () => void;
  } = $props();
  let draft = $state('');
  let error = $state('');
  let checked = $state('');
  let checking = $state(false);
  let dialog: HTMLDialogElement;
  let editor: HTMLDivElement;
  const bytes = $derived(new TextEncoder().encode(draft).length);

  onMount(() => {
    draft = initialText;
    dialog.showModal();
    let disposed = false;
    let view: import('@codemirror/view').EditorView | undefined;
    void (async () => {
      const [{ EditorView, basicSetup }, { jinja }, { oneDark }] = await Promise.all([
        import('codemirror'), import('@codemirror/lang-jinja'), import('@codemirror/theme-one-dark')
      ]);
      if (disposed) return;
      view = new EditorView({
        doc: draft,
        parent: editor,
        extensions: [basicSetup, jinja(), oneDark, EditorView.lineWrapping,
          EditorView.updateListener.of((update) => {
            if (update.docChanged) { draft = update.state.doc.toString(); checked = ''; error = ''; }
          })]
      });
      view.focus();
    })();
    return () => { disposed = true; view?.destroy(); dialog.close(); };
  });

  async function validate() {
    checking = true;
    error = '';
    checked = '';
    try {
      const response = await fetch('/v2/models/chat-template/validation', {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ text: draft })
      });
      const data = await response.json();
      if (!response.ok) throw new Error(String(data.detail ?? response.status));
      checked = data.detail;
    } catch (cause) { error = String(cause); }
    finally { checking = false; }
  }
</script>

<dialog bind:this={dialog} onclose={onClose} class="panel mono jinja-dialog" aria-label="Edit Jinja chat template">
  <div class="flex items-center justify-between gap-3 mb-2">
    <h2 class="text-[13px]">Custom Jinja chat template</h2>
    <button class="btn" type="button" onclick={() => dialog.close()} aria-label="Close editor">×</button>
  </div>
  <p class="text-[11px] mb-2" style="color: var(--muted)">Syntax highlighting is an aid; validate with the installed llama-server before applying.</p>
  <div bind:this={editor} class="jinja-editor" aria-label="Jinja source editor"></div>
  <div class="flex justify-between gap-2 mt-2 text-[11px]" style="color: var(--muted)">
    <span>{bytes.toLocaleString()} / 65,536 bytes</span><span>Model compatibility is not verified by syntax checks.</span>
  </div>
  {#if error}<p class="text-[11px] mt-2" role="alert" style="color: var(--danger)">{error}</p>{/if}
  {#if checked}<p class="text-[11px] mt-2" role="status" style="color: var(--accent)">{checked}</p>{/if}
  <div class="flex gap-2 justify-end mt-3">
    <button class="btn" type="button" onclick={() => dialog.close()}>Cancel</button>
    <button class="btn" type="button" disabled={checking || !draft || bytes > 65536} onclick={validate}>Validate</button>
    <button class="btn btn-accent" type="button" disabled={!draft || bytes > 65536} onclick={() => { onApply(draft); dialog.close(); }}>Apply</button>
  </div>
</dialog>

<style>
  .jinja-dialog { width: min(720px, calc(100vw - 24px)); max-height: calc(100vh - 24px); padding: 14px; color: var(--fg); margin: auto; }
  .jinja-dialog::backdrop { background: rgb(0 0 0 / 78%); }
  .jinja-editor { border: 1px solid var(--border); min-height: 240px; }
  .jinja-editor :global(.cm-editor) { height: min(50vh, 460px); font-size: 12px; }
</style>
