export const roles = [
  { id: 'lin', name: '林间', mark: 'L', tone: 'moss', x: 140, y: 270, visible: true, interactive: true },
  { id: 'yu', name: '余白', mark: 'Y', tone: 'clay', x: 470, y: 420, visible: true, interactive: true },
  { id: 'qiu', name: '秋实', mark: 'Q', tone: 'ink', x: 740, y: 230, visible: true, interactive: true }
];
export const events = [
  { speakerId: 'lin', text: '今天的桌面很安静。一起坐一会儿吧。' },
  { speakerId: 'yu', text: '我带了一本还没读完的书。你们想从哪一页开始？' },
  { speakerId: 'qiu', text: '不急，先听听你今天想说什么。' },
  { speakerId: 'lin', text: '如果把今天写成一个小故事，我想把窗边的位置留给你。这里是一条用于验证完整阅读的长消息：气泡只展示适合扫读的片段，所有文字仍可在对话面板中按原顺序找到。即使角色在屏幕边缘，或者已经离开当前场景，也不会改变这段话的身份与归属。我们不必挤在同一个位置，也能继续同一段对话。' },
  { move: { id: 'qiu', x: 9999, y: -40 }, speakerId: 'qiu', text: '我到边缘看看，消息仍然留在这里。' },
  { speakerId: 'yu', text: '我先离开一下。回看记录时，你仍然认得我。', remove: 'yu' },
  { speakerId: 'lin', text: '这就是固定样例的最后一句。可以回放，也可以写下你自己的开场。' }
];
