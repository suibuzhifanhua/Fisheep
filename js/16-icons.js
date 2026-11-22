// ═════════════════════════════════════════════════════════════════════════
// v1.0.665 图标体系（用户："继续优化ui以及各部件的图标 更高级 不要太粗糙"）
// 控件图标从 emoji 换成统一 inline SVG 线稿：24px 网格、1.6 描边、圆帽圆角、
// stroke=currentColor——颜色跟随主题（深/浅色无需各写一套），也才能吃
// hover 变色、disabled 变淡这些 CSS 状态。口径：
//  · 只换「可交互控件 + 结构位」（nav/输入条/操作栏/下拉/切换钮），
//    文案装饰 emoji（页面标题、帮助正文、toast）不动；
//  · STATUS_ICON 状态徽章：v1.0.741（界面审查 ①）起从 emoji 换成 icon() 线稿
//    （check/xmark/alert/refresh/circle/arrowup/minus），语义色由 01-core 的内联
//    span 走 currentColor 吃令牌；键集合仍被 test_status_vocabulary_has_one_source
//    锁死（ok/err/degraded/missing/installing/unknown/outdated/na/off 九键）；
//  · 上批 v1.0.664 给 emoji 刷的 --icon-shadow / filter:brightness 随之退役
//    （见 css/style.css 对应删除）。
// 加载位：index.html 顶部（在 00-boot 之前），供首屏静态 HTML 的替换器与
// 后续所有 js 共用。
// ═════════════════════════════════════════════════════════════════════════
const IC_SVG_OPEN = '<svg class="ic" viewBox="0 0 24 24" width="1em" height="1em" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">';
const IC_SVG_CLOSE = '</svg>';
// 统一 24 网格、2px 安全边；笔画 1.6 圆帽。fill 型（小实心点/三角）逐条自带属性。
const IC_PATHS = {
  // —— 侧栏导航 ——
  message: '<path d="M21 15a2 2 0 0 1-2 2H8l-4 4V5a2 2 0 0 1 2-2h13a2 2 0 0 1 2 2z"/>',
  gradcap: '<path d="M22 9L12 4 2 9l10 5z"/><path d="M6 11.4V16c0 1.7 2.7 3 6 3s6-1.3 6-3v-4.6"/>',
  bot: '<rect x="5" y="8" width="14" height="11" rx="2.5"/><path d="M12 8V5.5"/><circle cx="12" cy="3.6" r="1.1" fill="currentColor" stroke="none"/><path d="M9.2 13.3v1.4M14.8 13.3v1.4M2 12.5v3M22 12.5v3"/>',
  book: '<path d="M4 19.5A2.5 2.5 0 0 1 6.5 17H20"/><path d="M6.5 2H20v20H6.5A2.5 2.5 0 0 1 4 19.5v-15A2.5 2.5 0 0 1 6.5 2z"/>',
  plus: '<path d="M12 5v14M5 12h14"/>',
  cpu: '<rect x="6" y="6" width="12" height="12" rx="2"/><rect x="9.5" y="9.5" width="5" height="5" rx="1"/><path d="M9 2v2M15 2v2M9 20v2M15 20v2M2 9h2M2 15h2M20 9h2M20 15h2"/>',
  layout: '<rect x="3" y="3" width="18" height="18" rx="2.5"/><path d="M9 3v18M15 3v18"/>',
  sparkles: '<path d="M12 3l1.9 5.1L19 10l-5.1 1.9L12 17l-1.9-5.1L5 10l5.1-1.9z"/><path d="M18.6 15.4l.8 2.2 2.2.8-2.2.8-.8 2.2-.8-2.2-2.2-.8 2.2-.8z"/><path d="M4.9 15.9l.7 1.8 1.8.7-1.8.7-.7 1.8-.7-1.8-1.8-.7 1.8-.7z"/>',
  notebook: '<path d="M7 3.5h11a1.5 1.5 0 0 1 1.5 1.5v14a1.5 1.5 0 0 1-1.5 1.5H7a2.5 2.5 0 0 1-2.5-2.5V6A2.5 2.5 0 0 1 7 3.5z"/><path d="M10.2 3.5v17"/><path d="M13.2 8.5h4.6M13.2 12h4.6"/>',
  plug: '<path d="M9.5 2.5V6M14.5 2.5V6"/><path d="M6.5 6h11v3a5.5 5.5 0 0 1-5.5 5.5A5.5 5.5 0 0 1 6.5 9z"/><path d="M12 14.5V17"/><path d="M12 17a3 3 0 0 1-3 3H8.2A3 3 0 0 1 5.2 17"/>',
  folder: '<path d="M3 7.5A2.5 2.5 0 0 1 5.5 5H9l2.2 2.5h7.3A2.5 2.5 0 0 1 21 10v7.5a2.5 2.5 0 0 1-2.5 2.5h-13A2.5 2.5 0 0 1 3 17.5z"/>',
  clock: '<circle cx="12" cy="12" r="8.5"/><path d="M12 7.5V12l3 2"/>',
  wrench: '<path d="M14.7 6.3a1 1 0 0 0 0 1.4l1.6 1.6a1 1 0 0 0 1.4 0l3.77-3.77a6 6 0 0 1-7.94 7.94l-6.91 6.91a2.12 2.12 0 0 1-3-3l6.91-6.91a6 6 0 0 1 7.94-7.94z"/>',
  help: '<circle cx="12" cy="12" r="8.5"/><path d="M9.7 9.2a2.6 2.6 0 0 1 5 .9c0 1.7-2.2 2-2.2 3.4"/><path d="M12 16.8v.01"/>',
  gear: '<circle cx="12" cy="12" r="3.1"/><path d="M19.4 15a1.65 1.65 0 0 0 .33 1.82l.06.06a2 2 0 0 1-2.83 2.83l-.06-.06a1.65 1.65 0 0 0-1.82-.33 1.65 1.65 0 0 0-1 1.51V21a2 2 0 0 1-4 0v-.09A1.65 1.65 0 0 0 9 19.4a1.65 1.65 0 0 0-1.82.33l-.06.06a2 2 0 0 1-2.83-2.83l.06-.06a1.65 1.65 0 0 0 .33-1.82 1.65 1.65 0 0 0-1.51-1H3a2 2 0 0 1 0-4h.09A1.65 1.65 0 0 0 4.6 9a1.65 1.65 0 0 0-.33-1.82l-.06-.06a2 2 0 0 1 2.83-2.83l.06.06a1.65 1.65 0 0 0 1.82.33H9a1.65 1.65 0 0 0 1-1.51V3a2 2 0 0 1 4 0v.09a1.65 1.65 0 0 0 1 1.51 1.65 1.65 0 0 0 1.82-.33l.06-.06a2 2 0 0 1 2.83 2.83l-.06.06a1.65 1.65 0 0 0-.33 1.82V9a1.65 1.65 0 0 0 1.51 1H21a2 2 0 0 1 0 4h-.09a1.65 1.65 0 0 0-1.51 1z"/>',
  // —— 输入条 / 通用控件 ——
  paperclip: '<path d="M21.44 11.05l-9.19 9.19a6 6 0 0 1-8.49-8.49l9.19-9.19a4 4 0 0 1 5.66 5.66l-9.2 9.19a2 2 0 0 1-2.83-2.83l8.49-8.48"/>',
  mic: '<rect x="9" y="2.5" width="6" height="11" rx="3"/><path d="M5.5 11a6.5 6.5 0 0 0 13 0"/><path d="M12 17.5V21.5"/>',
  expand: '<path d="M8.5 3.5H5.5A2 2 0 0 0 3.5 5.5v3"/><path d="M15.5 3.5h3a2 2 0 0 1 2 2v3"/><path d="M15.5 20.5h3a2 2 0 0 0 2-2v-3"/><path d="M8.5 20.5h-3a2 2 0 0 1-2-2v-3"/>',
  send: '<path d="M12 19V6"/><path d="M6.5 11.5L12 6l5.5 5.5"/>',
  stop: '<rect x="6.8" y="6.8" width="10.4" height="10.4" rx="1.6" fill="currentColor" stroke="none"/>',
  search: '<circle cx="11" cy="11" r="6.5"/><path d="M20.5 20.5L16 16"/>',
  refresh: '<path d="M20.5 8.5A8.7 8.7 0 0 0 5.6 6.2L3.5 8.5"/><path d="M3.5 3.5v5h5"/><path d="M3.5 15.5a8.7 8.7 0 0 0 14.9 2.3l2.1-2.3"/><path d="M20.5 20.5v-5h-5"/>',
  chevron: '<path d="M6.5 9.5L12 15l5.5-5.5"/>',
  copy: '<rect x="9" y="9" width="11" height="11" rx="2"/><path d="M5.5 15H4.8A1.8 1.8 0 0 1 3 13.2V4.8A1.8 1.8 0 0 1 4.8 3h8.4A1.8 1.8 0 0 1 15 4.8v.7"/>',
  trash: '<path d="M4 7h16"/><path d="M9.5 7V5.2A1.2 1.2 0 0 1 10.7 4h2.6a1.2 1.2 0 0 1 1.2 1.2V7"/><path d="M6.3 7l.8 12.1a1.6 1.6 0 0 0 1.6 1.4h6.6a1.6 1.6 0 0 0 1.6-1.4L17.7 7"/><path d="M10.2 11v5.5M13.8 11v5.5"/>',
  edit: '<path d="M12 20h8"/><path d="M16.7 3.8a2.05 2.05 0 0 1 2.9 2.9L8.5 17.8 4.5 19.2 5.9 15.2z"/>',
  bulb: '<path d="M9.5 18h5"/><path d="M10.3 21h3.4"/><path d="M12 3a6 6 0 0 1 3.7 10.7c-.7.6-.9 1.3-.9 2.3h-5.6c0-1-.2-1.7-.9-2.3A6 6 0 0 1 12 3z"/>',
  save: '<path d="M19 21H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h11l5 5v11a2 2 0 0 1-2 2z"/><path d="M17 21v-8H7v8"/><path d="M7 3v5h8"/>',
  download: '<path d="M12 3.5V15"/><path d="M7 10.5L12 15.5 17 10.5"/><path d="M4.5 19.5v1a1.5 1.5 0 0 0 1.5 1.5h12a1.5 1.5 0 0 0 1.5-1.5v-1"/>',
  sun: '<circle cx="12" cy="12" r="3.6"/><path d="M12 2.5V5M12 19v2.5M2.5 12H5M19 12h2.5M5.3 5.3L7.1 7.1M16.9 16.9l1.8 1.8M18.7 5.3L16.9 7.1M7.1 16.9l-1.8 1.8"/>',
  moon: '<path d="M20.2 13.3A8.2 8.2 0 1 1 10.7 3.8a6.6 6.6 0 0 0 9.5 9.5z"/>',
  target: '<circle cx="12" cy="12" r="7.5"/><circle cx="12" cy="12" r="3" fill="currentColor" stroke="none"/><path d="M12 2v3M12 19v3M2 12h3M19 12h3"/>',
  // v1.0.667：调色盘——设置页「主题调色盘」卡标题位。
  palette: '<path d="M12 3a9 9 0 1 0 0 18c1.4 0 2.2-1 2.2-2.1 0-.9-.6-1.4-.6-2.3 0-1.1.9-1.9 2.1-1.9H17a4 4 0 0 0 4-4c0-4-4-7.7-9-7.7z"/><circle cx="7.5" cy="11" r="1.1" fill="currentColor" stroke="none"/><circle cx="10" cy="7.5" r="1.1" fill="currentColor" stroke="none"/><circle cx="14.5" cy="7" r="1.1" fill="currentColor" stroke="none"/>',
  camera: '<path d="M8.8 6.5l.9-1.7A1.6 1.6 0 0 1 11.1 4h1.8a1.6 1.6 0 0 1 1.4.8l.9 1.7"/><path d="M4 9A2.5 2.5 0 0 1 6.5 6.5h1.3l.9-1.7A1.6 1.6 0 0 1 10.1 4h3.8a1.6 1.6 0 0 1 1.4.8l.9 1.7h1.3A2.5 2.5 0 0 1 20 9v9a2.5 2.5 0 0 1-2.5 2.5h-11A2.5 2.5 0 0 1 4 18z"/><circle cx="12" cy="13.2" r="3.2"/>',
  layers: '<path d="M12 3.5L21.5 9 12 14.5 2.5 9z"/><path d="M2.5 13.5L12 19l9.5-5.5"/><path d="M2.5 17.5L12 23l9.5-5.5"/>',
  shield: '<path d="M12 3.5l7 2.6v5.4c0 4.4-3 7.7-7 9-4-1.3-7-4.6-7-9V6.1z"/>',
  brain: '<path d="M12 5a3 3 0 1 0-5.997.125 4 4 0 0 0-2.526 5.77 4 4 0 0 0 .556 6.588A4 4 0 1 0 12 18Z"/><path d="M12 5a3 3 0 1 1 5.997.125 4 4 0 0 1 2.526 5.77 4 4 0 0 1-.556 6.588A4 4 0 1 1 12 18Z"/><path d="M12 5v13"/>',
  // —— 工具卡分类（cat 串里的前缀字形）——
  openbook: '<path d="M2.5 5.5A2 2 0 0 1 4.5 4H11v15.5H4.5a2 2 0 0 0-2 1.5z"/><path d="M21.5 5.5A2 2 0 0 0 19.5 4H13v15.5h6.5a2 2 0 0 1 2 1.5z"/>',
  scale: '<path d="M12 4v16.5"/><path d="M8.5 20.5h7"/><path d="M4.5 7L12 4.5 19.5 7"/><path d="M4.5 7l-2 4.5a2.3 2.3 0 0 0 4.4 0z"/><path d="M19.5 7l-2 4.5a2.3 2.3 0 0 0 4.4 0z"/>',
  globe: '<circle cx="12" cy="12" r="8.5"/><path d="M3.5 12h17"/><path d="M12 3.5c2.4 2.4 3.6 5.3 3.6 8.5s-1.2 6.1-3.6 8.5c-2.4-2.4-3.6-5.3-3.6-8.5s1.2-6.1 3.6-8.5z"/>',
  mouse: '<rect x="8" y="2.5" width="8" height="19" rx="4"/><path d="M12 6.5v3.5"/>',
  check: '<path d="M4.5 12.5l4.8 4.8L19.5 6.5"/>',
  link: '<path d="M10.5 13.5a4 4 0 0 0 5.7 0l3-3a4 4 0 0 0-5.7-5.7l-1.5 1.5"/><path d="M13.5 10.5a4 4 0 0 0-5.7 0l-3 3a4 4 0 0 0 5.7 5.7l1.5-1.5"/>',
  // —— 工作台 ——
  panel: '<rect x="3.5" y="4.5" width="17" height="15" rx="2"/><path d="M9.8 4.5v15"/><path d="M12.6 8.8h3.4M12.6 12h3.4M12.6 15.2h2.2"/>',
  window: '<rect x="3.5" y="4.5" width="17" height="15" rx="2"/><path d="M3.5 9.5h17"/><path d="M6.8 7.1h.01M9.2 7.1h.01"/>',
  file: '<path d="M14 3.5H7a2 2 0 0 0-2 2v13a2 2 0 0 0 2 2h10a2 2 0 0 0 2-2V8.5z"/><path d="M14 3.5v5h5"/><path d="M9 13h6M9 16.2h6"/>',
  pointer: '<path d="M4 4l7.07 16.97 2.51-7.4L20.99 11z"/>',
  bolt: '<path d="M13 2.5L4.5 13.5h5.5l-1.5 8 8.5-11h-5.5z"/>',
  chart: '<path d="M4 20.5V4.5"/><path d="M4 20.5h16.5"/><path d="M8 16.5v-5M12.5 16.5V8M17 16.5v-3.5"/>',
  beaker: '<path d="M9.5 3h5"/><path d="M10 3v5.5L4.8 18a2 2 0 0 0 1.8 3h10.8a2 2 0 0 0 1.8-3L14 8.5V3"/><path d="M7.5 14.5h9"/>',
  // —— v1.0.666 全 UI 素色化：补齐渲染位用到的其余字形（同一 24 网格/1.6 描边口径）——
  users: '<circle cx="9" cy="8.5" r="3"/><path d="M3.5 19.5a5.5 5.5 0 0 1 11 0"/><path d="M15.5 6.1a3 3 0 0 1 0 4.8"/><path d="M17 14.6a5.5 5.5 0 0 1 3.5 4.9"/>',
  lock: '<rect x="5.5" y="10.5" width="13" height="9.5" rx="2"/><path d="M8.5 10.5V8a3.5 3.5 0 0 1 7 0v2.5"/>',
  lockOpen: '<rect x="5.5" y="10.5" width="13" height="9.5" rx="2"/><path d="M8.5 10.5V8a3.5 3.5 0 0 1 6.8-1.2"/>',
  unlock: '<rect x="5.5" y="10.5" width="13" height="9.5" rx="2"/><path d="M8.5 10.5V8a3.5 3.5 0 0 1 6.8-1.2"/>',
  user: '<circle cx="12" cy="8" r="3.5"/><path d="M5.5 20a6.5 6.5 0 0 1 13 0"/>',
  coins: '<circle cx="9" cy="9" r="5.5"/><path d="M9 6.5v5M7 8.2c0-.8.9-1.3 2-1.3s2 .5 2 1.2c0 1.6-4 .9-4 2.4 0 .8.9 1.3 2 1.3s2-.5 2-1.2"/><path d="M15.5 6.3a5.5 5.5 0 1 1-3.4 9.8"/>',
  banknote: '<rect x="2.5" y="6.5" width="19" height="11" rx="1.5"/><circle cx="12" cy="12" r="2.6"/><path d="M5.8 12h.01M18.2 12h.01"/>',
  receipt: '<path d="M6 3.5h12v17l-2-1.4-2 1.4-2-1.4-2 1.4-2-1.4-2 1.4z"/><path d="M9 8h6M9 11.5h6M9 15h4"/>',
  calendar: '<rect x="3.5" y="5" width="17" height="15.5" rx="2"/><path d="M3.5 9.5h17"/><path d="M8 3v4M16 3v4"/>',
  pen: '<path d="M3.5 20.5h17"/><path d="M15 4.5l4.5 4.5-10.4 10.4-5.2 1.3 1.3-5.2z"/>',
  doc: '<path d="M14 3.5H7a2 2 0 0 0-2 2v13a2 2 0 0 0 2 2h10a2 2 0 0 0 2-2V8.5z"/><path d="M14 3.5v5h5"/><path d="M9 12.5h6M9 15.8h4"/>',
  docCheck: '<path d="M14 3.5H7a2 2 0 0 0-2 2v13a2 2 0 0 0 2 2h10a2 2 0 0 0 2-2V8.5z"/><path d="M14 3.5v5h5"/><path d="M9 14.5l2.2 2.2 4-4.2"/>',
  docText: '<path d="M14 3.5H7a2 2 0 0 0-2 2v13a2 2 0 0 0 2 2h10a2 2 0 0 0 2-2V8.5z"/><path d="M14 3.5v5h5"/><path d="M9 11h6M9 14h6M9 17h3.5"/>',
  box: '<path d="M12 3.5l8 3.5v9l-8 3.5-8-3.5v-9z"/><path d="M4 7l8 3.5L20 7"/><path d="M12 10.5v9"/>',
  boxDn: '<path d="M12 3.5l8 3.5v9l-8 3.5-8-3.5v-9z"/><path d="M4 7l8 3.5L20 7"/><path d="M12 11v6.5M9.5 15.2l2.5 2.5 2.5-2.5"/>',
  boxUp: '<path d="M12 3.5l8 3.5v9l-8 3.5-8-3.5v-9z"/><path d="M4 7l8 3.5L20 7"/><path d="M12 17.5V11M9.5 13.3l2.5-2.5 2.5 2.5"/>',
  archive: '<rect x="3" y="4.5" width="18" height="5" rx="1"/><path d="M5 9.5v8.5a1.5 1.5 0 0 0 1.5 1.5h11A1.5 1.5 0 0 0 19 18V9.5"/><path d="M10 13.5h4"/>',
  package: '<path d="M12 3.5l8 3.5v9l-8 3.5-8-3.5v-9z"/><path d="M4 7l8 3.5L20 7"/><path d="M12 10.5v9"/><path d="M8 5.3l8 3.5"/>',
  stetho: '<path d="M5 3.5v5.5a4 4 0 0 0 8 0V3.5"/><path d="M9 13v3.5a5 5 0 0 0 5 5 4 4 0 0 0 4-4v-2.8"/><circle cx="18" cy="12" r="2.2"/>',
  archive2: '<rect x="3" y="4.5" width="18" height="5" rx="1"/><path d="M5 9.5v8.5a1.5 1.5 0 0 0 1.5 1.5h11A1.5 1.5 0 0 0 19 18V9.5"/><path d="M12 12.5v4M10 14.5l2 2 2-2"/>',
  clapper: '<rect x="3" y="9.5" width="18" height="10.5" rx="1.5"/><path d="M3.5 9.5L5 5.2l16 3-1 3.3"/><path d="M8.5 6.2l2 2.6M13.5 7.3l2 2.6"/>',
  sweep: '<path d="M14.5 10.5l6-6"/><path d="M14.5 10.5L10.8 14.2"/><path d="M4 20.5c1.2-3.2 3-4.8 6.8-5.3l3.9-3.9-2.4-2.4-3.9 3.9c-.5 3.8-2.2 5.6-4.4 7.7z"/>',
  chat: '<path d="M21 15a2 2 0 0 1-2 2H8l-4 4V5a2 2 0 0 1 2-2h13a2 2 0 0 1 2 2z"/><path d="M8 8.5h8M8 12h5"/>',
  mirror: '<circle cx="12" cy="11" r="7.5"/><path d="M8 7.8a4.5 4.5 0 0 1 2-1.3"/><path d="M12 18.5V21M8.5 21h7"/>',
  rocket: '<path d="M12 3.5c2.6 1.8 3.9 4.3 3.9 7.4 0 1.4-.3 2.7-.8 3.9H8.9a12 12 0 0 1-.8-3.9c0-3.1 1.3-5.6 3.9-7.4z"/><circle cx="12" cy="9.5" r="1.4"/><path d="M9 14.8L6.5 17.5v2l2.5-1.3M15 14.8l2.5 2.7v2L15 18.2"/><path d="M12 16.5v3.5"/>',
  bookshelf: '<path d="M5 4.5v15M12 4.5v15M19 4.5v15"/><path d="M4 20.5h16"/><path d="M7.5 7h2M7.5 10h2"/><path d="M14.5 8h2M14.5 11h2"/>',
  window2: '<rect x="3.5" y="4.5" width="17" height="15" rx="2"/><path d="M3.5 9h17"/><path d="M6.8 6.8h.01M9.2 6.8h.01"/>',
  calc: '<rect x="5.5" y="3" width="13" height="18" rx="2"/><path d="M8.5 7h7"/><path d="M9 12h.01M12 12h.01M15 12h.01M9 15.5h.01M12 15.5h.01M15 15.5h.01"/>',
  dna: '<path d="M8 3.5c0 6 8 7.5 8 13.5 0 2-1 3-1 3"/><path d="M16 3.5c0 6-8 7.5-8 13.5 0 2 1 3 1 3"/><path d="M9 6.5h6M9.5 10h5M11 15.5h2M10 19h4"/>',
  code: '<path d="M8.5 7.5L4 12l4.5 4.5M15.5 7.5L20 12l-4.5 4.5"/><path d="M13.2 5.5l-2.4 13"/>',
  mic2: '<rect x="9.3" y="3" width="5.4" height="10.5" rx="2.7"/><path d="M5.5 11.5a6.5 6.5 0 0 0 13 0"/><path d="M12 18v3"/>',
  headset: '<path d="M4.5 13.5a7.5 7.5 0 0 1 15 0"/><rect x="3.5" y="13.5" width="4" height="6" rx="1.5"/><rect x="16.5" y="13.5" width="4" height="6" rx="1.5"/>',
  tv: '<rect x="3" y="5.5" width="18" height="12" rx="2"/><path d="M8.5 20.5h7"/>',
  monitor: '<rect x="3.5" y="4.5" width="17" height="12.5" rx="2"/><path d="M12 17v3.5M8.5 20.5h7"/>',
  keyboard: '<rect x="2.5" y="7" width="19" height="11" rx="2"/><path d="M6 10.5h.01M9.5 10.5h.01M13 10.5h.01M16.5 10.5h.01M6 14.5h12"/>',
  laptop: '<rect x="4.5" y="5" width="15" height="10" rx="1.5"/><path d="M2.5 18.5h19"/>',
  news: '<path d="M4 5.5h13v13a2 2 0 0 1-2 2H6a2 2 0 0 1-2-2z"/><path d="M17 9.5h3v9a2 2 0 0 1-2 2"/><path d="M7 9h7M7 12.5h7M7 16h4.5"/>',
  tag: '<path d="M13 3.5H5a1.5 1.5 0 0 0-1.5 1.5v8L12.5 20a1.5 1.5 0 0 0 2.1 0l6.9-6.9a1.5 1.5 0 0 0 0-2.1z"/><circle cx="8.3" cy="8.3" r="1.2"/>',
  compass: '<circle cx="12" cy="12" r="8.5"/><path d="M15.5 8.5l-2 5-5 2 2-5z"/>',
  briefcase: '<rect x="3.5" y="8" width="17" height="12" rx="2"/><path d="M9 8V6a1.5 1.5 0 0 1 1.5-1.5h3A1.5 1.5 0 0 1 15 6v2"/><path d="M3.5 13h17"/>',
  trend: '<path d="M3.5 19.5h17"/><path d="M4.5 16.5l4.5-5 3.5 3 6.5-7.5"/><path d="M15.5 7h3.5v3.5"/>',
  pen2: '<path d="M14.5 5.5l4 4L8 20H4v-4z"/><path d="M12.5 7.5l4 4"/>',
  eye: '<path d="M2.5 12S6 5.5 12 5.5 21.5 12 21.5 12 18 18.5 12 18.5 2.5 12 2.5 12z"/><circle cx="12" cy="12" r="3"/>',
  mouse2: '<rect x="8" y="2.5" width="8" height="19" rx="4"/><path d="M12 6.5v3.5"/>',
  refresh2: '<path d="M20.5 12a8.5 8.5 0 1 1-2.5-6"/><path d="M20.5 3.5v4h-4"/>',
  box2: '<path d="M12 3.5l8 3.5v9l-8 3.5-8-3.5v-9z"/><path d="M4 7l8 3.5L20 7"/><path d="M12 10.5v9"/>',
  swap: '<path d="M4 8h13.5M14.5 4.5L18 8l-3.5 3.5"/><path d="M20 16H6.5M9.5 12.5L6 16l3.5 3.5"/>',
  upload: '<path d="M12 15V4.5"/><path d="M7 9.5L12 4.5 17 9.5"/><path d="M4.5 19.5v1a1.5 1.5 0 0 0 1.5 1.5h12a1.5 1.5 0 0 0 1.5-1.5v-1"/>',
  play: '<path d="M8 5.5l11 6.5-11 6.5z"/>',
  clock2: '<circle cx="12" cy="12" r="8.5"/><path d="M12 7.5V12l3 2"/>',
  // —— v1.0.715：状态栏 chip / 推理栏（替换彩色 emoji，同一 24 网格/1.6 描边口径）——
  xmark: '<path d="M6.5 6.5l11 11M17.5 6.5l-11 11"/>',
  pause: '<path d="M9.2 5.5v13M14.8 5.5v13"/>',
  alert: '<path d="M12 4L21.4 20H2.6z"/><path d="M12 10.2v4.2"/><path d="M12 17.4v.01"/>',
  // —— v1.0.741（界面审查 ①）：STATUS_ICON 状态徽章线稿化补齐（圆=未知/未启用，减号=不适用，上箭头=可更新）——
  circle: '<circle cx="12" cy="12" r="8.5"/>',
  minus: '<path d="M5.5 12h13"/>',
  arrowup: '<path d="M12 19.5V4.5"/><path d="M6 10.5l6-6 6 6"/>',
};
function icon(name, size){
  const p = IC_PATHS[name]; if(!p) return "";
  const w = size || "1em";
  return IC_SVG_OPEN.replace('width="1em" height="1em"', 'width="'+w+'" height="'+w+'"') + p + IC_SVG_CLOSE;
}
// v1.0.666（用户："带颜色的图标都换成统一素色的"）：把任意「图标值」翻译成素色线稿 SVG。
// 接受三种输入：① "data:NAME" ② 一个 emoji（查 EMOJI_IC_MAP）③ 一个已知线稿名。
// 未命中一律返回空串——宁可留白，也不再把彩色 emoji 原样画出来。size 透传给 icon()。
function icAny(value, size){
  if(!value) return "";
  let n = String(value);
  if(n.indexOf("data:")===0) n = n.slice(5);
  else if(IC_PATHS[n]===undefined) n = EMOJI_IC_MAP[n];
  if(!n || IC_PATHS[n]===undefined) return "";
  return icon(n, size);
}
// v1.0.666（用户："带颜色的图标都换成统一素色的"）：旧数据/旧入口存的彩色 emoji → 线稿名映射。
// 快捷入口 config.quick_links、内置技能 SKILLS、公共技能库等处的 emoji 图标渲染时经此翻译；
// 未收录的 emoji 渲染端留空（不再把彩色 emoji 原样画出来——那是"带颜色的图标"）。
const EMOJI_IC_MAP = {
  "🎓":"gradcap","🎯":"target","📝":"edit","📄":"doc","📥":"download","🔀":"swap",
  "💰":"banknote","🟢":"check","🧾":"receipt","🗓️":"calendar","🗓":"calendar","✨":"sparkles",
  "👁":"eye","🖱":"mouse","🌐":"globe","🗂":"folder","🧭":"compass","📋":"notebook",
  "📑":"docText","📊":"chart","📈":"trend","✍️":"pen","✍":"pen","🔍":"search","✅":"check",
  "📚":"book","🎓":"gradcap","🗄️":"box","🗄":"box","🖥":"monitor","💻":"laptop",
  "🌍":"globe","📰":"docText","🤖":"bot","📭":"doc","📌":"tag","⏱":"clock2",
};
// 首屏静态 HTML：index.html 用 <span data-ic="name">字形</span> 占位，
// 这里把占位整体换成 SVG（保留原字形作 title，辅助技术读得懂）。
function icHydrate(root){
  (root||document).querySelectorAll('[data-ic]').forEach(sp=>{
    const n = sp.getAttribute('data-ic');
    if(IC_PATHS[n]){ sp.innerHTML = icon(n); sp.setAttribute('role','img'); sp.setAttribute('aria-label', sp.dataset.icLabel || n); }
  });
}
document.addEventListener('DOMContentLoaded', function(){ icHydrate(document); });
