/**
 * `js/graph/layout.js` —— 纯几何,所以能整组机械断言,不必开浏览器。
 *
 * 两条最重要的性质:
 *
 *   - **确定性**:同一份数据两次布局必须逐像素相同。不确定 → 每次渲染节点换位 →
 *     用户点的是「甲」,点下去变成「乙」。径向布局会把这个问题放大到刺眼。
 *   - **不重叠**:同层任意两个盒子不得相交。这是「图能不能看懂」里唯一能被
 *     机械判定的部分(好不好看仍然要人看)。
 *
 * 两种布局共用一套断言 —— 契约统一正是为了让这件事成立。**不重叠 ≠ 好看**,
 * 「哪个布局更好看」这步没有替代品,必须人看(见 `choose()` 的阈值注释)。
 */
import { test } from 'node:test';
import assert from 'node:assert/strict';

import { loadKP, plain } from './harness.mjs';
import * as F from './fixtures/graphs.mjs';

const { KP } = await loadKP();
const { layered, radial, choose, layout, anchor, textWidth, wrapText,
        NODE_W, NODE_H, PAD, DIAG } = KP.layout;

/** 把 pos(Map) 、paths 等压成可深比的结构。 */
function snap(result) {
  return plain({
    pos: Array.from(result.pos.entries())
      .map(([id, b]) => [id, b.x, b.y, b.w, b.h])
      .sort((a, b) => String(a[0]).localeCompare(String(b[0]))),
    paths: result.paths,
    width: result.width,
    height: result.height,
    depths: result.depths,
    marks: result.marks,
    mode: result.mode,
  });
}

const overlaps = (a, b) =>
  a.x < b.x + b.w && b.x < a.x + a.w && a.y < b.y + b.h && b.y < a.y + a.h;

const CASES = ['chain', 'star', 'wideRing', 'cycle', 'emptyGraph', 'single',
               'fourStates', 'allMastered', 'xssGraph', 'pathological', 'longName'];

/** 两种布局的并集 —— 「两种都成立」的性质都用它。 */
const BOTH = [['layered', layered], ['radial', radial]];

test('确定性:同一输入两次布局逐字段相同', () => {
  for (const [m, fn] of BOTH) {
    for (const name of CASES) {
      const g = F.ALL[name]();
      assert.deepEqual(snap(fn(g.nodes, g.edges)), snap(fn(g.nodes, g.edges)),
        `${m}/${name} 的布局不确定 —— 节点会在两次渲染之间换位`);
    }
  }
});

test('确定性:节点输入顺序被打乱也不改变结果(列/环内按 order_index + name 排)', () => {
  for (const [m, fn] of BOTH) {
    const g = F.wideRing(8);
    const shuffled = [...g.nodes].reverse();
    assert.deepEqual(snap(fn(shuffled, g.edges)), snap(fn(g.nodes, g.edges)), `${m} 对输入顺序敏感`);
  }
});

test('包含性:所有盒子落在 [0,width] × [0,height] 之内', () => {
  for (const [m, fn] of BOTH) {
    for (const name of CASES) {
      const g = F.ALL[name]();
      const L = fn(g.nodes, g.edges);
      for (const [id, b] of L.pos) {
        assert.ok(b.x >= 0 && b.y >= 0, `${m}/${name}/${id} 坐标为负`);
        assert.ok(b.x + b.w <= L.width, `${m}/${name}/${id} 右越界`);
        assert.ok(b.y + b.h <= L.height, `${m}/${name}/${id} 下越界`);
      }
    }
  }
});

test('不重叠:同一份夹具里任意两个盒子都不相交', () => {
  for (const [m, fn] of BOTH) {
    for (const name of CASES) {
      const g = F.ALL[name]();
      const L = fn(g.nodes, g.edges);
      const boxes = Array.from(L.pos.entries());
      for (let i = 0; i < boxes.length; i += 1) {
        for (let j = i + 1; j < boxes.length; j += 1) {
          assert.ok(!overlaps(boxes[i][1], boxes[j][1]),
            `${m}/${name}:${boxes[i][0]} 与 ${boxes[j][0]} 重叠`);
        }
      }
    }
  }
});

test('同层巨宽(13 个节点)也不重叠 —— 径向最坏情况', () => {
  // 一层挤 13 个盒子是两种布局共同的最坏输入。径向的保证是
  // 「弦长 ≥ 盒子对角线」,这条用例是那个推导的实证。
  const g = F.wideRing(13);
  for (const [m, fn] of BOTH) {
    const L = fn(g.nodes, g.edges);
    assert.equal(L.depths.length, 2, `${m} 的层数不对`);
    assert.equal(L.pos.size, 14, `${m} 的盒子数不对`);
    const at = (d) => g.nodes.filter((n) => n.depth === d).map((n) => L.pos.get(n.id));
    assert.equal(at(0).length, 1);
    assert.equal(at(1).length, 13);
    for (const a of at(1)) for (const b of at(1)) if (a !== b) assert.ok(!overlaps(a, b), `${m} 环上重叠`);
  }
});

test('每个盒子都是固定的 NODE_W × NODE_H(名字长度不影响布局)', () => {
  for (const [m, fn] of BOTH) {
    for (const name of CASES) {
      const g = F.ALL[name]();
      for (const [, b] of fn(g.nodes, g.edges).pos) {
        assert.equal(b.w, NODE_W, m);
        assert.equal(b.h, NODE_H, m);
      }
    }
  }
});

test('长名(200 字)不改变任何盒子的位置或画布尺寸', () => {
  // 节点名没有长度上限(`_clean_name` 只剥编号不截断)。横向布局用固定 NODE_W
  // 天然免疫;径向布局的**环半径**若被名字宽度撑开,整张图会当场炸掉 ——
  // 半径只由层数与环内节点数决定,与名字无关,这条把两种布局都钉住。
  for (const [m, fn] of BOTH) {
    const long = snap(fn(F.longName(200).nodes, F.longName(200).edges));
    const short = snap(fn(F.longName(2).nodes, F.longName(2).edges));
    assert.deepEqual(long, short, `${m} 的布局被名字长度撑变了`);
    assert.equal(long.pos.length, 3);
  }
});

test('边:两端都在图里才生成 path,坐标不含 NaN', () => {
  for (const [m, fn] of BOTH) {
    const g = F.chain(5);
    const L = fn(g.nodes, g.edges);
    assert.equal(L.paths.length, 4, m);
    for (const p of L.paths) {
      assert.ok(!/NaN|undefined/.test(p.d), `${m} 的 path 里出现了非法坐标:${p.d}`);
      for (const v of [p.from.x, p.from.y, p.to.x, p.to.y]) {
        assert.ok(Number.isFinite(v), `${m} 的端点坐标不是有限数`);
      }
      assert.equal(p.source, g.edges.find((e) => `${e.source_id}>${e.target_id}` === p.id).source_id);
    }
  }
});

test('边:端点缺失时整条跳过,而不是画到原点', () => {
  for (const [m, fn] of BOTH) {
    const g = F.chain(3);
    g.edges.push({ source_id: 'c0', target_id: '不存在', relation: '前置' });
    assert.equal(fn(g.nodes, g.edges).paths.length, 2, `${m} 的悬空边应当被丢弃`);
  }
});

test('空图不抛,尺寸退化成 PAD 级别', () => {
  for (const [m, fn] of BOTH) {
    const L = fn([], []);
    assert.equal(L.pos.size, 0, m);
    assert.equal(L.paths.length, 0, m);
    assert.ok(L.width > 0 && L.height > 0, m);
    assert.equal(L.depths.length, 0, m);
  }
});

test('反向边(A↔B 环)不抛,两个盒子都在,且被标成 back', () => {
  // 反向边只由破环时的强制放行产生(`path.py:219-222`)。它必须被**标出来**:
  // 按正向画的话,一条线会横穿整张图(分层里从右往左横穿,径向里从外往内横穿)。
  for (const [m, fn] of BOTH) {
    const g = F.cycle();
    const L = fn(g.nodes, g.edges);
    assert.equal(L.pos.size, 2, m);
    assert.equal(L.paths.length, 2, m);
    const back = L.paths.filter((p) => p.back);
    assert.equal(back.length, 1, `${m} 里有 ${back.length} 条反向边,应当恰好 1 条`);
    assert.equal(back[0].id, 'a>b', m);
  }
});

test('textWidth:CJK 比 ASCII 宽', () => {
  assert.ok(textWidth('中', 14) > textWidth('a', 14));
  assert.equal(textWidth('中', 14), 14);
  assert.equal(textWidth('', 14), 0);
});

test('wrapText:按宽度折行,超行数用省略号截断', () => {
  assert.deepEqual(plain(wrapText('甲乙丙丁', 14, 14 * 2, 2)), ['甲乙', '丙丁']);
  const long = wrapText('甲'.repeat(10), 14, 14 * 2, 2);
  assert.equal(long.length, 2);
  assert.ok(long[1].endsWith('…'), long[1]);
});

test('wrapText:空值不抛', () => {
  assert.deepEqual(plain(wrapText(null, 14, 100, 2)), []);
  assert.deepEqual(plain(wrapText('', 14, 100, 2)), []);
});

// ---- 统一契约 ----------------------------------------------------------------

test('统一契约:两种布局返回同一组键,渲染器因此不关心跑了哪个', () => {
  const KEYS = ['depths', 'height', 'marks', 'mode', 'paths', 'pos', 'width'];
  for (const [m, fn] of BOTH) {
    const L = fn(F.fourStates().nodes, F.fourStates().edges);
    assert.deepEqual(Object.keys(L).sort(), KEYS, `${m} 的契约与另一种布局不一致`);
    // 别用 `instanceof Map` —— 布局跑在 vm 沙箱里,那个 Map 是这个 realm 的
    // `Map` 的**另一个**构造器,`instanceof` 恒为假。用结构判定。
    assert.equal(typeof L.pos.get, 'function', `${m}:pos 必须支持 .get()`);
    assert.equal(typeof L.pos.size, 'number', `${m}:pos 必须有 .size`);
    assert.equal(L.mode, m);
    assert.equal(typeof L.width, 'number');
    assert.equal(typeof L.height, 'number');
    for (const p of L.paths) {
      assert.deepEqual(Object.keys(p).sort(),
        ['back', 'd', 'from', 'id', 'source', 'target', 'to'], `${m} 的 path 字段不一致`);
    }
  }
});

test('marks:layered 每层一个几何锚点,文字由视图层拼;radial 不给环标注', () => {
  // 布局只给 `{x,y,level}`,「第 N 层」这几个字归视图层 —— 布局不该知道文案。
  // 径向刻意不给:环标注要么压在环上的节点上,要么挤在环之间的缝里,而角度随
  // 节点数变化,没有稳的落点(理由写在 `radial()` 里)。
  const g = F.fourStates();
  const L = layered(g.nodes, g.edges);
  // `L.depths` 是沙箱 realm 的数组,两侧都得过一遍 `plain()` 才能深比。
  assert.deepEqual(plain(L.marks.map((mk) => mk.level)), plain(L.depths));
  assert.equal(L.marks.length, L.depths.length);
  for (const mk of L.marks) {
    assert.ok(Number.isFinite(mk.x) && Number.isFinite(mk.y));
    assert.ok(mk.y < PAD, '层标注不该压进节点区');
  }
  assert.deepEqual(plain(radial(g.nodes, g.edges).marks), []);
});

// ---- 锚点:连线与盒子的交点(两种布局共用) ----------------------------------

test('anchor:射线先在哪个轴上碰到边界,取哪个轴', () => {
  const box = { x: 0, y: 0, w: 100, h: 40 };
  // 正右方 → 右边中点;正上方 → 上边中点。
  assert.deepEqual(plain(anchor(box, { x: 500, y: 20 })), { x: 100, y: 20 });
  assert.deepEqual(plain(anchor(box, { x: 50, y: -500 })), { x: 50, y: 0 });
  // 45° 斜射:宽高比 2.5:1,先碰上边。
  const diag = anchor(box, { x: 150, y: -80 });
  assert.equal(diag.y, 0);
  assert.ok(diag.x > 50 && diag.x < 100);
});

test('anchor:朝中心方向退化成中心点本身,不产生 NaN', () => {
  const box = { x: 0, y: 0, w: 100, h: 40 };
  assert.deepEqual(plain(anchor(box, { x: 50, y: 20 })), { x: 50, y: 20 });
});

test('锚点:每条边的两端都落在各自盒子的边界上(不是中心,也不是写死的左右边)', () => {
  // 拆分前这里是「源取右边缘中点、目标取左边缘中点」—— 在分层里看着还行,
  // 到径向就完全错(连线该指向各个方向)。改成真正的边界求交后两种布局通用,
  // 判据:端点到中心的**归一化切比雪夫距离**恰好为 1 ⟺ 落在边界上。
  const onBoundary = (p, b) => {
    const nx = Math.abs(p.x - (b.x + b.w / 2)) / (b.w / 2);
    const ny = Math.abs(p.y - (b.y + b.h / 2)) / (b.h / 2);
    return Math.abs(Math.max(nx, ny) - 1) < 1e-6;
  };
  for (const [m, fn] of BOTH) {
    for (const name of ['star', 'wideRing', 'fourStates', 'chain']) {
      const g = F.ALL[name]();
      const L = fn(g.nodes, g.edges);
      for (const p of L.paths) {
        assert.ok(onBoundary(p.from, L.pos.get(p.source)), `${m}/${name}:${p.id} 的起点不在源框边界上`);
        assert.ok(onBoundary(p.to, L.pos.get(p.target)), `${m}/${name}:${p.id} 的终点不在目标框边界上`);
      }
    }
  }
});

// ---- 径向:把「中心」这件事钉成几何性质,而不是靠肉眼 ------------------------

/** 盒子中心。 */
const ctr = (b) => ({ x: b.x + b.w / 2, y: b.y + b.h / 2 });
const dist = (a, b) => Math.hypot(a.x - b.x, a.y - b.y);

test('径向:单根落在环系的中心 —— 根节点中心 == 各叶子中心的质心', () => {
  // 设计稿要「中心节点 = 最基础的那一个」。它不需要合成元素:单根时那个位置
  // 本来就是它本人。这里用**质心**而不是包围盒中心 —— 角度从正上方起奇数个
  // 节点时包围盒上下不对称,质心才是「绕着它排」的那一点。
  const g = F.star(11);
  const L = radial(g.nodes, g.edges);
  const root = ctr(L.pos.get('root'));
  const leaves = g.nodes.filter((n) => n.depth === 1).map((n) => ctr(L.pos.get(n.id)));
  assert.equal(leaves.length, 11);
  const mean = leaves.reduce((acc, p) => ({ x: acc.x + p.x / leaves.length, y: acc.y + p.y / leaves.length }),
                             { x: 0, y: 0 });
  assert.ok(dist(mean, root) < 1e-6, `根不在环系中心:偏移 ${dist(mean, root)}`);
});

test('径向:环内等距环绕,半径 ≥ 盒子对角线(不重叠那条推导的前提)', () => {
  const g = F.star(11);
  const L = radial(g.nodes, g.edges);
  const root = ctr(L.pos.get('root'));
  const radii = g.nodes.filter((n) => n.depth === 1).map((n) => dist(ctr(L.pos.get(n.id)), root));
  for (const r of radii) {
    assert.ok(Math.abs(r - radii[0]) < 1e-6, '同一环上的节点没落在同一个圆上');
    assert.ok(r >= DIAG, `环半径 ${r} < 对角线 ${DIAG},环上盒子可能重叠`);
  }
});

test('径向:环号 = depth,由内向外单调扩张', () => {
  const g = F.fourStates();   // u(0) → s/r(1) → m(2)
  const L = radial(g.nodes, g.edges);
  const root = ctr(L.pos.get('u'));
  const r = (id) => dist(ctr(L.pos.get(id)), root);
  assert.ok(r('s') < r('m'), '外环没有比内环更远');
  assert.ok(r('s') > 0, 'depth-1 的节点落在圆心上了');
  assert.ok(Math.abs(r('s') - r('r')) < 1e-6, '同一层的两个节点半径不同');
});

test('径向:12 节点链能画、仍不重叠,但画布被撑到几千像素 —— 这正是 choose() 要挡的形状', () => {
  // 这条不是「确保径向能用」,而是把 `choose()` 的存在理由钉成可执行的证据:
  // 每环只站 1 个节点的纯链,环半径逐层 +DIAG 线性增长,11 环之后画布上万像素。
  // 它没错(无重叠、包含性都成立),只是难看且没法看 —— 而好看与否只能人看,
  // 所以机器守住的边界就到这里:`choose()` 会把它交给 layered。
  const g = F.chain(12);
  const L = radial(g.nodes, g.edges);
  assert.equal(L.pos.size, 12);
  // 每环只站 1 个节点时角度恒为 -90°(正上方),于是 12 个盒子叠成一条竖线。
  const xs = new Set(Array.from(L.pos.values()).map((b) => b.x.toFixed(6)));
  assert.equal(xs.size, 1, '单节点环本该落在同一条竖直轴线上');
  assert.ok(L.height > 2000, `画布高仅 ${L.height},退化程度与预期不符`);
  assert.equal(choose(g.nodes), 'layered', '这个形状必须交给 layered');
});

// ---- choose():按形状选布局 ---------------------------------------------------

test('choose:链式 / 层多的走 layered,星形 / 宽环走 radial', () => {
  assert.equal(choose(F.chain(12).nodes), 'layered', '12 层 12 节点是纯链,径向会退化成同心圆');
  assert.equal(choose(F.star(11).nodes), 'radial', '1 根 11 叶是径向的招牌形态');
  assert.equal(choose(F.wideRing(12).nodes), 'radial');
  assert.equal(choose(F.cycle().nodes), 'layered', '2 节点 2 层,径向没有意义');
  assert.equal(choose(F.single().nodes), 'layered');
  assert.equal(choose(F.fourStates().nodes), 'layered');
  assert.equal(choose(F.allMastered().nodes), 'layered');
  assert.equal(choose([]), 'layered', '空图不该走径向分支');
  assert.equal(choose(null), 'layered');
});

test('choose:layout() 默认按形状走,显式 mode 可覆盖(给人工对比两种模式用)', () => {
  const g = F.star(11);
  assert.equal(layout(g.nodes, g.edges).mode, 'radial');
  assert.equal(layout(g.nodes, g.edges, 'layered').mode, 'layered');
  // 显式覆盖必须真的换布局,而不只是改了个 mode 字段。
  assert.deepEqual(snap(layout(g.nodes, g.edges, 'layered')), snap(layered(g.nodes, g.edges)));
  assert.deepEqual(snap(layout(g.nodes, g.edges, 'radial')), snap(radial(g.nodes, g.edges)));
  // 未知 mode 值退回自动选择,不抛。
  assert.equal(layout(g.nodes, g.edges, 'nope').mode, 'radial');
});

test('choose:两条判据的边界都可断言(改阈值就会红)', () => {
  // 阈值是经验值,但它决定了「用户会看到哪个布局」,所以不能是随手可改的魔数 ——
  // 改它就得同时改这几条,也就必须解释为什么。
  const shaped = (...counts) => {
    const nodes = [];
    counts.forEach((per, d) => {
      for (let k = 0; k < per; k += 1) {
        nodes.push(F.node({ id: `d${d}k${k}`, order_index: nodes.length, depth: d }));
      }
    });
    return nodes;
  };
  // 判据一:`levels > 6`。7 层一律 layered,哪怕每层只站一个。
  assert.equal(choose(shaped(...Array(7).fill(1))), 'layered', '7 层应当强行走 layered');
  // 边界是**严格**大于:恰好 6 层且密度低时仍走 radial。
  assert.equal(choose(shaped(1, ...Array(5).fill(20))), 'radial', '恰好 6 层不该触发层数判据');
  // 判据二:密度 0.55。3/9 = 0.33 → radial;3/4 = 0.75 → layered。
  assert.equal(choose(shaped(3, 3, 3)), 'radial', '3 层 9 节点:0.33 ≤ 0.55');
  assert.equal(choose(shaped(2, 1, 1)), 'layered', '3 层 4 节点:0.75 > 0.55');
  assert.equal(choose(shaped(1, 8)), 'radial', '2 层 9 节点:0.22 ≤ 0.55');
  assert.equal(choose(shaped(1, 1, 1)), 'layered', '3 层 3 节点:1.0 > 0.55');
});

// ---- Stage 7:中心环画在哪儿(设计稿 §12 / 计划 D2) ------------------------------

test('centerOf:单根时中心就是**那个真实节点**,而且它真的在包围盒中点上', () => {
  // 这条是「中心环不能画歪」的全部保证。径向的节点全部落在以原点为心的同心环上,
  // 所以包围盒关于原点对称 —— 于是「包围盒中点」与「原点」是同一个点(不是近似)。
  // 一旦哪天 `radial()` 改了平移方式,这条会红,而环画歪在截图上看不出来。
  // 浮点:`width / 2` 与「平移后的盒子中心」是两条不同的加法路径,末位可能差一个
  // ulp(本机实测差 3e-14)。这里断言的是「同一个点」,不是「同一条算式」,所以给容差。
  const near = (a, b, msg) => assert.ok(Math.abs(a - b) < 1e-6, `${msg}(${a} vs ${b})`);
  for (const [name, g] of [['star', F.star(4)], ['single', F.single()]]) {
    const lay = radial(g.nodes, g.edges);
    const c = KP.layout.centerOf(g, lay);
    near(c.x, lay.width / 2, `${name}: 中心 x 不在包围盒中线上`);
    near(c.y, lay.height / 2, `${name}: 中心 y 不在包围盒中线上`);
    assert.ok(c.node, `${name}: 单根却没有拿到中心节点`);
    assert.equal(c.node.id, g.nodes[0].id);
    // 环套的是**那个盒子的中心** —— 盒子中心与圆心不重合的话,环会偏心,而截图
    // 上看起来只是「差一点点」。这条才是「画在哪儿」的真正断言。
    const box = lay.pos.get(c.node.id);
    near(box.x + box.w / 2, c.x, `${name}: 环的圆心与盒子中心不重合`);
    near(box.y + box.h / 2, c.y, `${name}: 环的圆心与盒子中心不重合`);
  }
});

test('centerOf:横向布局没有「中心」这个概念,返回 null', () => {
  // 图从左往右长,「中心」不是一个确定的位置。硬找一个出来只会让环画在一个
  // 跟语义无关的地方。
  const g = F.chain(4);
  assert.equal(KP.layout.centerOf(g, layered(g.nodes, g.edges)), null);
  assert.equal(KP.layout.centerOf(g, null), null);
  assert.equal(KP.layout.centerOf(g, {}), null);
});

test('centerOf:0 个或 ≥2 个 depth-0 时不认领中心节点(但坐标仍然给)', () => {
  // 计划 D2 原本写的是「这时合成一个 topic 中心节点」。**没有这么做**:径向的环
  // 半径只保证「相邻两环 ≥ DIAG」,原点处没有留位置 —— 环 0 站 2 个节点时半径是
  // DIAG/2,比一个盒子还窄,往原点硬塞必然重叠。所以这里只给坐标、`node` 为 null,
  // 视图层据此不画环(见 `views/graph.js` 的 `centerRingHTML`)。
  const twoRoots = F.graph(
    [F.node({ id: 'a', depth: 0, order_index: 0 }), F.node({ id: 'b', depth: 0, order_index: 1 })],
    [], { title: '双根' });
  const c2 = KP.layout.centerOf(twoRoots, radial(twoRoots.nodes, twoRoots.edges));
  assert.equal(c2.node, null);
  assert.equal(typeof c2.x, 'number');
  const noRoot = F.graph([F.node({ id: 'x', depth: 1 })], [], { title: '无根' });
  const c0 = KP.layout.centerOf(noRoot, radial(noRoot.nodes, noRoot.edges));
  assert.equal(c0.node, null);
  // 单根而 depth 是脏数据(字符串 '0'、负数)时也要认得出来 —— `depthOf` 归一过。
  const dirty = F.graph([F.node({ id: 'd', depth: '-0', order_index: 0 })], [], {});
  assert.ok(KP.layout.centerOf(dirty, radial(dirty.nodes, dirty.edges)).node);
});
