<script lang="ts">
  const paths: Record<string, string> = {
    cluster: 'M9 3h6v6H9z M2 15h6v6H2z M16 15h6v6h-6z M12 9v3 M5 15v-3h14v3',
    cube: 'm12 3 9 5v8l-9 5-9-5V8z M3 8l9 5 9-5 M12 13v8 M7.5 5.5l9 5',
    rocket: 'M14 4c2-1 4-1 6-1 0 2 0 4-1 6l-7 7-5-5z M14 4H8l-4 5 3 2 M19 9v6l-5 5-2-4 M5 15l-2 6 6-2 M15 8h.01',
    activity: 'M3 12h4l3-8 4 16 3-8h4',
    plug: 'M8 3v5 M16 3v5 M6 8h12v4a6 6 0 0 1-12 0z M12 18v4',
    settings: 'M12 8a4 4 0 1 0 0 8 4 4 0 0 0 0-8 M9 3h6l1 3 3 1 2 5-2 5-3 1-1 3H9l-1-3-3-1-2-5 2-5 3-1z',
    chevron: 'm9 5 7 7-7 7',
    down: 'm6 9 6 6 6-6',
    monitor: 'M3 4h18v13H3z M12 17v4 M8 21h8',
    server: 'M4 3h16v7H4z M4 14h16v7H4z M7 6.5h.01 M7 17.5h.01 M16 6.5h2 M16 17.5h2',
    chip: 'M8 3v3 M16 3v3 M8 18v3 M16 18v3 M3 8h3 M3 16h3 M18 8h3 M18 16h3 M7 7h10v10H7z',
    menu: 'M4 6h16 M4 12h16 M4 18h16',
    close: 'm6 6 12 12 M18 6 6 18',
    info: 'M12 2a10 10 0 1 0 0 20 10 10 0 0 0 0-20 M12 11v6 M12 7h.01',
    circleCheck: 'M22 11a10 10 0 1 1-5.9-9.1 M22 3l-10 10-3-3',
    clock: 'M12 2a10 10 0 1 0 0 20 10 10 0 0 0 0-20 M12 6v6l4 2',
    alert: 'm12 3 10 18H2z M12 9v5 M12 17h.01',
    network: 'M12 3v18 M3 12h18 M4 5l16 14 M4 19 20 5',
    shield: 'm12 2 9 4v6c0 5-9 10-9 10S3 17 3 12V6z m-5 10 3 3 7-7',
    plus: 'M12 5v14 M5 12h14',
    arrow: 'M4 12h16 m-6-6 6 6-6 6',
    list: 'M9 5h12 M9 12h12 M9 19h12 M3 5h.01 M3 12h.01 M3 19h.01',
    search: 'M11 3a8 8 0 1 0 0 16 8 8 0 0 0 0-16 m6 14 4 4',
    download: 'M12 3v12 m-5-5 5 5 5-5 M4 16v5h16v-5',
    copy: 'M9 9h12v12H9z M15 5V3H3v12h2',
    check: 'm5 12 4 4L19 6',
    send: 'm22 2-7 20-4-9-9-4z M22 2 11 13',
    stop: 'M5 5h14v14H5z',
    play: 'm7 3 14 9-14 9z',
    pause: 'M8 4v16 M16 4v16',
    trash: 'M3 6h18 M9 6V3h6v3 M5 6l1 15h12l1-15 M10 10v7 M14 10v7',
    back: 'M20 12H4 m6-6-6 6 6 6',
    key: 'M14 5a5 5 0 1 0 0 10 5 5 0 0 0 0-10 M10 14l-7 7v-4h4v-3',
    chat: 'M3 3h18v14H8l-5 4z M7 8h10 M7 12h6'
  };

  let { name, small = false }: { name: string; small?: boolean } = $props();
</script>

<svg class="icon {small ? 'small-icon' : ''}" viewBox="0 0 24 24" aria-hidden="true">
  <path d={paths[name] ?? paths.cube} />
</svg>
