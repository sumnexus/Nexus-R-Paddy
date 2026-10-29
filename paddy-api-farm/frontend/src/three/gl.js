// ─────────────────────────────────────────────────────────────────────────────
// gl.js — copied from the Agri Robotics Playground (src/ui/gl.js) for the Paddy API Farm v2, as an ES module.
// Changes: `export`; the orbit camera also answers the keyboard (arrows orbit, Shift+arrows or WASD pan, +/− zoom,
// PageUp/PageDown tilt) and can be told to move instantly (prefers-reduced-motion); the canvas can keep its drawing
// buffer for screenshots. The renderer itself is unchanged.
// ─────────────────────────────────────────────────────────────────────────────
export const GX = (() => {
  'use strict';

  // ── math (column-major 4×4, like WebGL) ──
  const M4 = {
    ident: () => new Float32Array([1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1]),
    mul(a, b, o = new Float32Array(16)) {
      for (let c = 0; c < 4; c++) for (let r = 0; r < 4; r++) {
        o[c * 4 + r] = a[r] * b[c * 4] + a[4 + r] * b[c * 4 + 1] + a[8 + r] * b[c * 4 + 2] + a[12 + r] * b[c * 4 + 3];
      }
      return o;
    },
    perspective(fovy, aspect, n, f) {
      const t = 1 / Math.tan(fovy / 2), o = new Float32Array(16);
      o[0] = t / aspect; o[5] = t; o[10] = (f + n) / (n - f); o[11] = -1; o[14] = 2 * f * n / (n - f);
      return o;
    },
    ortho(l, r, b, t, n, f) {
      const o = new Float32Array(16);
      o[0] = 2 / (r - l); o[5] = 2 / (t - b); o[10] = -2 / (f - n); o[12] = -(r + l) / (r - l); o[13] = -(t + b) / (t - b); o[14] = -(f + n) / (f - n); o[15] = 1;
      return o;
    },
    lookAt(e, c, u) {
      let zx = e[0] - c[0], zy = e[1] - c[1], zz = e[2] - c[2];
      let l = Math.hypot(zx, zy, zz) || 1; zx /= l; zy /= l; zz /= l;
      let xx = u[1] * zz - u[2] * zy, xy = u[2] * zx - u[0] * zz, xz = u[0] * zy - u[1] * zx;
      l = Math.hypot(xx, xy, xz) || 1; xx /= l; xy /= l; xz /= l;
      const yx = zy * xz - zz * xy, yy = zz * xx - zx * xz, yz = zx * xy - zy * xx;
      return new Float32Array([xx, yx, zx, 0, xy, yy, zy, 0, xz, yz, zz, 0, -(xx * e[0] + xy * e[1] + xz * e[2]), -(yx * e[0] + yy * e[1] + yz * e[2]), -(zx * e[0] + zy * e[1] + zz * e[2]), 1]);
    },
    invert(m) {
      const o = new Float32Array(16);
      const a00 = m[0], a01 = m[1], a02 = m[2], a03 = m[3], a10 = m[4], a11 = m[5], a12 = m[6], a13 = m[7];
      const a20 = m[8], a21 = m[9], a22 = m[10], a23 = m[11], a30 = m[12], a31 = m[13], a32 = m[14], a33 = m[15];
      const b00 = a00 * a11 - a01 * a10, b01 = a00 * a12 - a02 * a10, b02 = a00 * a13 - a03 * a10, b03 = a01 * a12 - a02 * a11;
      const b04 = a01 * a13 - a03 * a11, b05 = a02 * a13 - a03 * a12, b06 = a20 * a31 - a21 * a30, b07 = a20 * a32 - a22 * a30;
      const b08 = a20 * a33 - a23 * a30, b09 = a21 * a32 - a22 * a31, b10 = a21 * a33 - a23 * a31, b11 = a22 * a33 - a23 * a32;
      let det = b00 * b11 - b01 * b10 + b02 * b09 + b03 * b08 - b04 * b07 + b05 * b06;
      if (!det) return M4.ident();
      det = 1 / det;
      o[0] = (a11 * b11 - a12 * b10 + a13 * b09) * det; o[1] = (a02 * b10 - a01 * b11 - a03 * b09) * det;
      o[2] = (a31 * b05 - a32 * b04 + a33 * b03) * det; o[3] = (a22 * b04 - a21 * b05 - a23 * b03) * det;
      o[4] = (a12 * b08 - a10 * b11 - a13 * b07) * det; o[5] = (a00 * b11 - a02 * b08 + a03 * b07) * det;
      o[6] = (a32 * b02 - a30 * b05 - a33 * b01) * det; o[7] = (a20 * b05 - a22 * b02 + a23 * b01) * det;
      o[8] = (a10 * b10 - a11 * b08 + a13 * b06) * det; o[9] = (a01 * b08 - a00 * b10 - a03 * b06) * det;
      o[10] = (a30 * b04 - a31 * b02 + a33 * b00) * det; o[11] = (a21 * b02 - a20 * b04 - a23 * b00) * det;
      o[12] = (a11 * b07 - a10 * b09 - a12 * b06) * det; o[13] = (a00 * b09 - a01 * b07 + a02 * b06) * det;
      o[14] = (a31 * b01 - a30 * b03 - a32 * b00) * det; o[15] = (a20 * b03 - a21 * b01 + a22 * b00) * det;
      return o;
    },
    // translation · rotY · rotX · rotZ · scale, written straight into `o` at offset `k`
    trs(o, k, tx, ty, tz, ry, sx, sy, sz, rx = 0, rz = 0) {
      const cy = Math.cos(ry), sy_ = Math.sin(ry), cx = Math.cos(rx), sx_ = Math.sin(rx), cz = Math.cos(rz), sz_ = Math.sin(rz);
      // R = Ry * Rx * Rz
      const r00 = cy * cz + sy_ * sx_ * sz_, r01 = -cy * sz_ + sy_ * sx_ * cz, r02 = sy_ * cx;
      const r10 = cx * sz_, r11 = cx * cz, r12 = -sx_;
      const r20 = -sy_ * cz + cy * sx_ * sz_, r21 = sy_ * sz_ + cy * sx_ * cz, r22 = cy * cx;
      o[k] = r00 * sx; o[k + 1] = r10 * sx; o[k + 2] = r20 * sx; o[k + 3] = 0;
      o[k + 4] = r01 * sy; o[k + 5] = r11 * sy; o[k + 6] = r21 * sy; o[k + 7] = 0;
      o[k + 8] = r02 * sz; o[k + 9] = r12 * sz; o[k + 10] = r22 * sz; o[k + 11] = 0;
      o[k + 12] = tx; o[k + 13] = ty; o[k + 14] = tz; o[k + 15] = 1;
    },
    // a unit box along +Y (height 1, centred on y = 0.5) stretched from p to q, thickness w×d
    between(o, k, p, q, w, d, roll = 0) {
      const dx = q[0] - p[0], dy = q[1] - p[1], dz = q[2] - p[2], L = Math.hypot(dx, dy, dz) || 1e-6;
      const ux = dx / L, uy = dy / L, uz = dz / L; // new Y axis
      // pick a helper not parallel to Y
      let hx = 0, hy = 0, hz = 1; if (Math.abs(uz) > 0.9) { hx = 1; hz = 0; }
      let xx = uy * hz - uz * hy, xy = uz * hx - ux * hz, xz = ux * hy - uy * hx; let l = Math.hypot(xx, xy, xz) || 1; xx /= l; xy /= l; xz /= l;
      let zx = xy * uz - xz * uy, zy = xz * ux - xx * uz, zz = xx * uy - xy * ux;
      if (roll) { const c = Math.cos(roll), s = Math.sin(roll); const nx = [xx * c + zx * s, xy * c + zy * s, xz * c + zz * s], nz = [zx * c - xx * s, zy * c - xy * s, zz * c - xz * s];[xx, xy, xz] = nx;[zx, zy, zz] = nz; }
      o[k] = xx * w; o[k + 1] = xy * w; o[k + 2] = xz * w; o[k + 3] = 0;
      o[k + 4] = ux * L; o[k + 5] = uy * L; o[k + 6] = uz * L; o[k + 7] = 0;
      o[k + 8] = zx * d; o[k + 9] = zy * d; o[k + 10] = zz * d; o[k + 11] = 0;
      o[k + 12] = p[0]; o[k + 13] = p[1]; o[k + 14] = p[2]; o[k + 15] = 1;
    },
  };
  const srgb = v => (v <= 0.04045 ? v / 12.92 : Math.pow((v + 0.055) / 1.055, 2.4));
  const _lin = new Map();
  function hexLin(hex) {
    let v = _lin.get(hex);
    if (!v) {
      const h = hex.replace('#', ''), n = parseInt(h.length === 3 ? h.split('').map(c => c + c).join('') : h, 16);
      v = [srgb(((n >> 16) & 255) / 255), srgb(((n >> 8) & 255) / 255), srgb((n & 255) / 255)];
      _lin.set(hex, v);
    }
    return v.slice();
  }
  const mixLin = (a, b, t) => [a[0] + (b[0] - a[0]) * t, a[1] + (b[1] - a[1]) * t, a[2] + (b[2] - a[2]) * t];

  // ── shaders ──
  const LIT_VS = `#version 300 es
  precision highp float;
  layout(location=0) in vec3 aPos; layout(location=1) in vec3 aNor; layout(location=2) in vec3 aCol; layout(location=3) in vec2 aUV;
  layout(location=4) in vec4 iM0; layout(location=5) in vec4 iM1; layout(location=6) in vec4 iM2; layout(location=7) in vec4 iM3;
  layout(location=8) in vec4 iCol; layout(location=9) in vec4 iPar;
  uniform mat4 uVP; uniform mat4 uLVP; uniform float uTime; uniform float uWind;
  out vec3 vN; out vec3 vC; out vec3 vW; out vec4 vL; out vec2 vUV; out float vE; out float vA;
  void main(){
    mat4 M = mat4(iM0, iM1, iM2, iM3);
    vec3 p = aPos;
    float h = max(p.y, 0.0);
    float sway = iPar.x * uWind * h * h;
    p.x += sway * sin(uTime * 1.9 + iPar.y);
    p.z += sway * 0.6 * cos(uTime * 1.37 + iPar.y * 1.7);
    vec4 w = M * vec4(p, 1.0);
    vW = w.xyz; vN = mat3(M) * aNor; vC = aCol * iCol.rgb; vA = iCol.a; vE = iPar.z; vUV = aUV;
    vL = uLVP * w;
    gl_Position = uVP * w;
  }`;
  const LIT_FS = `#version 300 es
  precision highp float; precision highp sampler2DShadow;
  in vec3 vN; in vec3 vC; in vec3 vW; in vec4 vL; in vec2 vUV; in float vE; in float vA;
  uniform vec3 uSunDir; uniform vec3 uSunCol; uniform vec3 uSky; uniform vec3 uGround; uniform vec3 uCam; uniform vec3 uFog; uniform float uFogD; uniform float uFogStart;
  uniform sampler2DShadow uShadow; uniform float uShadowOn; uniform vec2 uTexel;
  uniform sampler2D uMap; uniform float uUseMap; uniform float uSpec; uniform float uUnlit; uniform float uExposure; uniform float uAlphaMul;
  out vec4 o;
  float shadowF(){
    vec3 p = vL.xyz / vL.w * 0.5 + 0.5;
    if (p.x < 0.0 || p.x > 1.0 || p.y < 0.0 || p.y > 1.0 || p.z > 1.0) return 1.0;
    float s = 0.0;
    for (int x = -1; x <= 1; x++) for (int y = -1; y <= 1; y++) s += texture(uShadow, vec3(p.xy + vec2(x, y) * uTexel, p.z - 0.0016));
    return s / 9.0;
  }
  void main(){
    vec3 base = vC;
    if (uUseMap > 0.5) base *= texture(uMap, vUV).rgb;
    vec3 col;
    if (uUnlit > 0.5) col = base * (1.0 + vE);
    else {
      vec3 N = normalize(vN); if (!gl_FrontFacing) N = -N;
      vec3 L = normalize(uSunDir);
      float ndl = dot(N, L);
      float sh = uShadowOn > 0.5 ? shadowF() : 1.0;
      float diff = max(ndl, 0.0) * sh + max(-ndl, 0.0) * 0.12; // a little light through leaves
      vec3 hemi = mix(uGround, uSky, N.y * 0.5 + 0.5);
      vec3 V = normalize(uCam - vW), H = normalize(L + V);
      float spec = pow(max(dot(N, H), 0.0), 48.0) * uSpec * sh;
      col = base * (hemi + uSunCol * diff) + uSunCol * spec + base * vE;
    }
    float d = max(0.0, length(uCam - vW) - uFogStart); // haze measured from near the orbit target, so zooming out doesn't wash out the farm
    float f = 1.0 - exp(-pow(d * uFogD, 1.5));
    col = mix(col, uFog, clamp(f, 0.0, 1.0));
    col = vec3(1.0) - exp(-col * uExposure);
    o = vec4(pow(col, vec3(1.0 / 2.2)), vA * uAlphaMul);
  }`;
  const DEPTH_VS = `#version 300 es
  precision highp float;
  layout(location=0) in vec3 aPos; layout(location=4) in vec4 iM0; layout(location=5) in vec4 iM1; layout(location=6) in vec4 iM2; layout(location=7) in vec4 iM3; layout(location=9) in vec4 iPar;
  uniform mat4 uLVP; uniform float uTime; uniform float uWind;
  void main(){
    mat4 M = mat4(iM0, iM1, iM2, iM3); vec3 p = aPos; float h = max(p.y, 0.0); float sway = iPar.x * uWind * h * h;
    p.x += sway * sin(uTime * 1.9 + iPar.y); p.z += sway * 0.6 * cos(uTime * 1.37 + iPar.y * 1.7);
    gl_Position = uLVP * M * vec4(p, 1.0);
  }`;
  const DEPTH_FS = `#version 300 es
  precision highp float; void main(){}`;
  const SKY_VS = `#version 300 es
  layout(location=0) in vec2 aP; out vec2 vP; void main(){ vP = aP; gl_Position = vec4(aP, 0.99999, 1.0); }`;
  const SKY_FS = `#version 300 es
  precision highp float;
  in vec2 vP; out vec4 o;
  uniform mat4 uInvVP; uniform vec3 uCam; uniform vec3 uSunDir; uniform vec3 uZenith; uniform vec3 uHorizon; uniform vec3 uGroundCol; uniform vec3 uSunCol;
  uniform float uCloud; uniform float uTime; uniform float uNight; uniform float uExposure; uniform float uSunVis;
  float hash(vec2 p){ return fract(sin(dot(p, vec2(127.1, 311.7))) * 43758.5453); }
  float noise(vec2 p){ vec2 i = floor(p), f = fract(p); vec2 u = f * f * (3.0 - 2.0 * f);
    return mix(mix(hash(i), hash(i + vec2(1, 0)), u.x), mix(hash(i + vec2(0, 1)), hash(i + vec2(1, 1)), u.x), u.y); }
  float fbm(vec2 p){ float s = 0.0, a = 0.5; for (int i = 0; i < 5; i++){ s += a * noise(p); p *= 2.03; a *= 0.5; } return s; }
  void main(){
    vec4 a = uInvVP * vec4(vP, 1.0, 1.0); vec3 dir = normalize(a.xyz / a.w - uCam);
    float y = dir.y;
    vec3 col = mix(uHorizon, uZenith, pow(clamp(y, 0.0, 1.0), 0.5));
    if (y < 0.0) col = mix(uHorizon, uGroundCol, clamp(-y * 5.0, 0.0, 1.0));
    float sd = max(dot(dir, normalize(uSunDir)), 0.0);
    col += uSunCol * (pow(sd, 1400.0) * 30.0 + pow(sd, 18.0) * 0.35) * (1.0 - uCloud * 0.85) * uSunVis;
    if (y > 0.0) {
      vec2 uv = dir.xz / (y + 0.15) * 1.25 + vec2(uTime * 0.006, uTime * 0.0021);
      float n = fbm(uv);
      float cov = smoothstep(0.78 - uCloud * 0.62, 1.02 - uCloud * 0.45, n);
      vec3 lit = mix(vec3(1.0, 0.98, 0.95), vec3(0.62, 0.64, 0.68), uCloud) * mix(1.0, 0.07, uNight);
      col = mix(col, lit * (0.55 + 0.45 * uHorizon), cov * smoothstep(0.0, 0.18, y) * 0.92);
    }
    if (uNight > 0.01 && y > 0.0) {
      vec3 q = floor(dir * 380.0);
      float h = fract(sin(dot(q, vec3(12.9898, 78.233, 37.719))) * 43758.5453);
      col += vec3(step(0.9982, h)) * uNight * (1.0 - uCloud) * smoothstep(0.0, 0.25, y) * 1.4;
    }
    col = vec3(1.0) - exp(-col * uExposure);
    o = vec4(pow(col, vec3(1.0 / 2.2)), 1.0);
  }`;
  const LINE_VS = `#version 300 es
  layout(location=0) in vec3 aPos; layout(location=1) in vec4 aCol; uniform mat4 uVP; out vec4 vC;
  void main(){ vC = aCol; gl_Position = uVP * vec4(aPos, 1.0); }`;
  const LINE_FS = `#version 300 es
  precision highp float; in vec4 vC; out vec4 o; void main(){ o = vC; }`;
  const PT_VS = `#version 300 es
  layout(location=0) in vec3 aPos; layout(location=1) in vec4 aCol; layout(location=2) in float aSize;
  uniform mat4 uVP; uniform vec3 uCam; uniform float uScale; out vec4 vC;
  void main(){ vC = aCol; vec4 p = uVP * vec4(aPos, 1.0); gl_Position = p; gl_PointSize = clamp(aSize * uScale / max(0.5, p.w), 1.0, 64.0); }`;
  const PT_FS = `#version 300 es
  precision highp float; in vec4 vC; out vec4 o;
  void main(){ vec2 d = gl_PointCoord - 0.5; float r = dot(d, d) * 4.0; if (r > 1.0) discard; o = vec4(vC.rgb, vC.a * (1.0 - r)); }`;

  class Engine {
    constructor(canvas, opts = {}) {
      const gl = canvas.getContext('webgl2', { antialias: true, alpha: false, powerPreference: 'high-performance', preserveDrawingBuffer: !!opts.preserve });
      if (!gl) throw new Error('WebGL2 is not available in this browser.');
      this.gl = gl; this.canvas = canvas;
      this.progLit = this._program(LIT_VS, LIT_FS);
      this.progDepth = this._program(DEPTH_VS, DEPTH_FS);
      this.progSky = this._program(SKY_VS, SKY_FS);
      this.progLine = this._program(LINE_VS, LINE_FS);
      this.progPt = this._program(PT_VS, PT_FS);
      this.u = {};
      for (const [name, p] of Object.entries({ lit: this.progLit, depth: this.progDepth, sky: this.progSky, line: this.progLine, pt: this.progPt })) {
        const n = gl.getProgramParameter(p, gl.ACTIVE_UNIFORMS), map = {};
        for (let i = 0; i < n; i++) { const info = gl.getActiveUniform(p, i); map[info.name] = gl.getUniformLocation(p, info.name); }
        this.u[name] = map;
      }
      // sky triangle
      this.skyVao = gl.createVertexArray(); gl.bindVertexArray(this.skyVao);
      const sb = gl.createBuffer(); gl.bindBuffer(gl.ARRAY_BUFFER, sb); gl.bufferData(gl.ARRAY_BUFFER, new Float32Array([-1, -1, 3, -1, -1, 3]), gl.STATIC_DRAW);
      gl.enableVertexAttribArray(0); gl.vertexAttribPointer(0, 2, gl.FLOAT, false, 0, 0);
      gl.bindVertexArray(null);
      this.shadowSize = opts.shadowSize || 2048; this.shadowOn = opts.shadows !== false;
      this._makeShadow(this.shadowSize);
      // 1×1 white texture for meshes without a map
      this.white = this.texture(1, 1, new Uint8Array([255, 255, 255, 255]));
      gl.enable(gl.DEPTH_TEST); gl.enable(gl.CULL_FACE);
      this.sets = []; this.lines = []; this.points = [];
      this.stats = { draws: 0, tris: 0 };
    }
    _program(vs, fs) {
      const gl = this.gl;
      const sh = (type, src) => { const s = gl.createShader(type); gl.shaderSource(s, src); gl.compileShader(s); if (!gl.getShaderParameter(s, gl.COMPILE_STATUS)) throw new Error(gl.getShaderInfoLog(s) + '\n' + src.split('\n').slice(0, 4).join('\n')); return s; };
      const p = gl.createProgram();
      gl.attachShader(p, sh(gl.VERTEX_SHADER, vs)); gl.attachShader(p, sh(gl.FRAGMENT_SHADER, fs)); gl.linkProgram(p);
      if (!gl.getProgramParameter(p, gl.LINK_STATUS)) throw new Error(gl.getProgramInfoLog(p));
      return p;
    }
    _makeShadow(size) {
      const gl = this.gl;
      if (this.shadowTex) { gl.deleteTexture(this.shadowTex); gl.deleteFramebuffer(this.shadowFbo); }
      this.shadowSize = size;
      this.shadowTex = gl.createTexture(); gl.bindTexture(gl.TEXTURE_2D, this.shadowTex);
      gl.texStorage2D(gl.TEXTURE_2D, 1, gl.DEPTH_COMPONENT24, size, size);
      gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MIN_FILTER, gl.LINEAR); gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MAG_FILTER, gl.LINEAR);
      gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_WRAP_S, gl.CLAMP_TO_EDGE); gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_WRAP_T, gl.CLAMP_TO_EDGE);
      gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_COMPARE_MODE, gl.COMPARE_REF_TO_TEXTURE); gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_COMPARE_FUNC, gl.LEQUAL);
      this.shadowFbo = gl.createFramebuffer(); gl.bindFramebuffer(gl.FRAMEBUFFER, this.shadowFbo);
      gl.framebufferTexture2D(gl.FRAMEBUFFER, gl.DEPTH_ATTACHMENT, gl.TEXTURE_2D, this.shadowTex, 0);
      gl.drawBuffers([gl.NONE]); gl.readBuffer(gl.NONE);
      gl.bindFramebuffer(gl.FRAMEBUFFER, null);
    }
    setShadowQuality(on, size) { this.shadowOn = on; if (on && size && size !== this.shadowSize) this._makeShadow(size); }
    texture(w, h, data, nearest = false) {
      const gl = this.gl, t = gl.createTexture();
      gl.bindTexture(gl.TEXTURE_2D, t);
      gl.texImage2D(gl.TEXTURE_2D, 0, gl.RGBA, w, h, 0, gl.RGBA, gl.UNSIGNED_BYTE, data);
      const f = nearest ? gl.NEAREST : gl.LINEAR;
      gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MIN_FILTER, f); gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MAG_FILTER, f);
      gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_WRAP_S, gl.CLAMP_TO_EDGE); gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_WRAP_T, gl.CLAMP_TO_EDGE);
      return { tex: t, w, h, nearest };
    }
    updateTexture(t, data, nearest) {
      const gl = this.gl; gl.bindTexture(gl.TEXTURE_2D, t.tex);
      gl.texSubImage2D(gl.TEXTURE_2D, 0, 0, 0, t.w, t.h, gl.RGBA, gl.UNSIGNED_BYTE, data);
      if (nearest != null && nearest !== t.nearest) { const f = nearest ? gl.NEAREST : gl.LINEAR; gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MIN_FILTER, f); gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MAG_FILTER, f); t.nearest = nearest; }
    }
    // geo: {pos, nor, col (linear rgb), uv?, idx}
    mesh(geo) {
      const gl = this.gl, m = { count: geo.idx.length };
      gl.bindVertexArray(null); // never disturb another VAO's element binding
      m.bufs = ['pos', 'nor', 'col', 'uv'].map(k => { const b = gl.createBuffer(); gl.bindBuffer(gl.ARRAY_BUFFER, b); gl.bufferData(gl.ARRAY_BUFFER, geo[k] || new Float32Array((geo.pos.length / 3) * (k === 'uv' ? 2 : 3)), gl.STATIC_DRAW); return b; });
      m.ibo = gl.createBuffer(); gl.bindBuffer(gl.ELEMENT_ARRAY_BUFFER, m.ibo);
      const big = geo.pos.length / 3 > 65535;
      gl.bufferData(gl.ELEMENT_ARRAY_BUFFER, big ? Uint32Array.from(geo.idx) : Uint16Array.from(geo.idx), gl.STATIC_DRAW);
      m.itype = big ? gl.UNSIGNED_INT : gl.UNSIGNED_SHORT;
      m.tris = geo.idx.length / 3;
      return m;
    }
    // An instanced draw set. mat: {spec, unlit, map, transparent, additive, castShadow, receiveShadow, doubleSided, depthWrite, order}
    instanced(mesh, max, mat = {}) {
      const gl = this.gl;
      const set = { mesh, max, count: 0, M: new Float32Array(16 * max), C: new Float32Array(4 * max).fill(1), P: new Float32Array(4 * max), mat: Object.assign({ spec: 0.15, castShadow: true, doubleSided: false, transparent: false, additive: false, depthWrite: true, order: 0, alpha: 1 }, mat), dirtyM: true, dirtyC: true, dirtyP: true, visible: true };
      set.vao = gl.createVertexArray(); gl.bindVertexArray(set.vao);
      const attr = (buf, loc, size) => { gl.bindBuffer(gl.ARRAY_BUFFER, buf); gl.enableVertexAttribArray(loc); gl.vertexAttribPointer(loc, size, gl.FLOAT, false, 0, 0); };
      attr(mesh.bufs[0], 0, 3); attr(mesh.bufs[1], 1, 3); attr(mesh.bufs[2], 2, 3); attr(mesh.bufs[3], 3, 2);
      gl.bindBuffer(gl.ELEMENT_ARRAY_BUFFER, mesh.ibo);
      set.bM = gl.createBuffer(); gl.bindBuffer(gl.ARRAY_BUFFER, set.bM); gl.bufferData(gl.ARRAY_BUFFER, set.M.byteLength, gl.DYNAMIC_DRAW);
      for (let i = 0; i < 4; i++) { gl.enableVertexAttribArray(4 + i); gl.vertexAttribPointer(4 + i, 4, gl.FLOAT, false, 64, i * 16); gl.vertexAttribDivisor(4 + i, 1); }
      set.bC = gl.createBuffer(); gl.bindBuffer(gl.ARRAY_BUFFER, set.bC); gl.bufferData(gl.ARRAY_BUFFER, set.C.byteLength, gl.DYNAMIC_DRAW);
      gl.enableVertexAttribArray(8); gl.vertexAttribPointer(8, 4, gl.FLOAT, false, 0, 0); gl.vertexAttribDivisor(8, 1);
      set.bP = gl.createBuffer(); gl.bindBuffer(gl.ARRAY_BUFFER, set.bP); gl.bufferData(gl.ARRAY_BUFFER, set.P.byteLength, gl.DYNAMIC_DRAW);
      gl.enableVertexAttribArray(9); gl.vertexAttribPointer(9, 4, gl.FLOAT, false, 0, 0); gl.vertexAttribDivisor(9, 1);
      gl.bindVertexArray(null);
      set.set = (i, k, v) => { set[k][i] = v; };
      this.sets.push(set);
      return set;
    }
    remove(set) { const i = this.sets.indexOf(set); if (i >= 0) this.sets.splice(i, 1); }
    _upload(set) {
      const gl = this.gl;
      if (set.dirtyM) { gl.bindBuffer(gl.ARRAY_BUFFER, set.bM); gl.bufferSubData(gl.ARRAY_BUFFER, 0, set.M, 0, set.count * 16); set.dirtyM = false; }
      if (set.dirtyC) { gl.bindBuffer(gl.ARRAY_BUFFER, set.bC); gl.bufferSubData(gl.ARRAY_BUFFER, 0, set.C, 0, set.count * 4); set.dirtyC = false; }
      if (set.dirtyP) { gl.bindBuffer(gl.ARRAY_BUFFER, set.bP); gl.bufferSubData(gl.ARRAY_BUFFER, 0, set.P, 0, set.count * 4); set.dirtyP = false; }
    }
    lineBuffer(max) {
      const gl = this.gl, lb = { max, n: 0, P: new Float32Array(max * 3), C: new Float32Array(max * 4), visible: true, depthTest: true };
      lb.vao = gl.createVertexArray(); gl.bindVertexArray(lb.vao);
      lb.bP = gl.createBuffer(); gl.bindBuffer(gl.ARRAY_BUFFER, lb.bP); gl.bufferData(gl.ARRAY_BUFFER, lb.P.byteLength, gl.DYNAMIC_DRAW); gl.enableVertexAttribArray(0); gl.vertexAttribPointer(0, 3, gl.FLOAT, false, 0, 0);
      lb.bC = gl.createBuffer(); gl.bindBuffer(gl.ARRAY_BUFFER, lb.bC); gl.bufferData(gl.ARRAY_BUFFER, lb.C.byteLength, gl.DYNAMIC_DRAW); gl.enableVertexAttribArray(1); gl.vertexAttribPointer(1, 4, gl.FLOAT, false, 0, 0);
      gl.bindVertexArray(null);
      lb.push = (x1, y1, z1, x2, y2, z2, r, g, b, a, a2 = a) => {
        if (lb.n + 2 > lb.max) return;
        const i = lb.n * 3, j = lb.n * 4;
        lb.P[i] = x1; lb.P[i + 1] = y1; lb.P[i + 2] = z1; lb.P[i + 3] = x2; lb.P[i + 4] = y2; lb.P[i + 5] = z2;
        lb.C[j] = r; lb.C[j + 1] = g; lb.C[j + 2] = b; lb.C[j + 3] = a; lb.C[j + 4] = r; lb.C[j + 5] = g; lb.C[j + 6] = b; lb.C[j + 7] = a2;
        lb.n += 2;
      };
      this.lines.push(lb);
      return lb;
    }
    pointBuffer(max) {
      const gl = this.gl, pb = { max, n: 0, P: new Float32Array(max * 3), C: new Float32Array(max * 4), S: new Float32Array(max), visible: true, additive: false };
      pb.vao = gl.createVertexArray(); gl.bindVertexArray(pb.vao);
      pb.bP = gl.createBuffer(); gl.bindBuffer(gl.ARRAY_BUFFER, pb.bP); gl.bufferData(gl.ARRAY_BUFFER, pb.P.byteLength, gl.DYNAMIC_DRAW); gl.enableVertexAttribArray(0); gl.vertexAttribPointer(0, 3, gl.FLOAT, false, 0, 0);
      pb.bC = gl.createBuffer(); gl.bindBuffer(gl.ARRAY_BUFFER, pb.bC); gl.bufferData(gl.ARRAY_BUFFER, pb.C.byteLength, gl.DYNAMIC_DRAW); gl.enableVertexAttribArray(1); gl.vertexAttribPointer(1, 4, gl.FLOAT, false, 0, 0);
      pb.bS = gl.createBuffer(); gl.bindBuffer(gl.ARRAY_BUFFER, pb.bS); gl.bufferData(gl.ARRAY_BUFFER, pb.S.byteLength, gl.DYNAMIC_DRAW); gl.enableVertexAttribArray(2); gl.vertexAttribPointer(2, 1, gl.FLOAT, false, 0, 0);
      gl.bindVertexArray(null);
      this.points.push(pb);
      return pb;
    }

    // env: {sunDir, sunCol, sky, ground, fog, fogD, exposure, zenith, horizon, cloud, night, time, wind, shadowBox:{cx,cz,r}, sunVis}
    render(cam, env) {
      const gl = this.gl, W = this.canvas.width, H = this.canvas.height;
      this.stats.draws = 0; this.stats.tris = 0;
      // light matrices
      const sd = env.sunDir, r = env.shadowBox.r;
      const center = [env.shadowBox.cx, 0, env.shadowBox.cz];
      const eye = [center[0] + sd[0] * 80, center[1] + sd[1] * 80, center[2] + sd[2] * 80];
      const up = Math.abs(sd[1]) > 0.98 ? [0, 0, 1] : [0, 1, 0];
      const lv = M4.lookAt(eye, center, up), lp = M4.ortho(-r, r, -r, r, 1, 200);
      const lvp = M4.mul(lp, lv);
      for (const s of this.sets) if (s.visible && s.count) this._upload(s);
      const shadows = this.shadowOn && env.shadows !== false && env.sunDir[1] > 0.04;
      if (shadows) {
        gl.bindFramebuffer(gl.FRAMEBUFFER, this.shadowFbo);
        gl.viewport(0, 0, this.shadowSize, this.shadowSize);
        gl.clear(gl.DEPTH_BUFFER_BIT);
        gl.useProgram(this.progDepth);
        const u = this.u.depth;
        gl.uniformMatrix4fv(u.uLVP, false, lvp); gl.uniform1f(u.uTime, env.time); gl.uniform1f(u.uWind, env.wind);
        gl.enable(gl.POLYGON_OFFSET_FILL); gl.polygonOffset(1.6, 3.0);
        gl.disable(gl.CULL_FACE);
        for (const s of this.sets) {
          if (!s.visible || !s.count || !s.mat.castShadow || s.mat.transparent) continue;
          gl.bindVertexArray(s.vao); gl.drawElementsInstanced(gl.TRIANGLES, s.mesh.count, s.mesh.itype, 0, s.count);
        }
        gl.disable(gl.POLYGON_OFFSET_FILL);
        gl.bindFramebuffer(gl.FRAMEBUFFER, null);
      }
      gl.viewport(0, 0, W, H);
      gl.clearColor(0, 0, 0, 1); gl.clear(gl.COLOR_BUFFER_BIT | gl.DEPTH_BUFFER_BIT);
      // sky
      gl.useProgram(this.progSky);
      {
        const u = this.u.sky;
        gl.uniformMatrix4fv(u.uInvVP, false, M4.invert(cam.vp)); gl.uniform3fv(u.uCam, cam.eye); gl.uniform3fv(u.uSunDir, env.sunDir);
        gl.uniform3fv(u.uZenith, env.zenith); gl.uniform3fv(u.uHorizon, env.horizon); gl.uniform3fv(u.uGroundCol, env.ground); gl.uniform3fv(u.uSunCol, env.sunCol);
        gl.uniform1f(u.uCloud, env.cloud); gl.uniform1f(u.uTime, env.time); gl.uniform1f(u.uNight, env.night); gl.uniform1f(u.uExposure, env.exposure); gl.uniform1f(u.uSunVis, env.sunVis);
        gl.depthMask(false); gl.bindVertexArray(this.skyVao); gl.drawArrays(gl.TRIANGLES, 0, 3); gl.depthMask(true);
      }
      // lit sets
      gl.useProgram(this.progLit);
      const u = this.u.lit;
      gl.uniformMatrix4fv(u.uVP, false, cam.vp); gl.uniformMatrix4fv(u.uLVP, false, lvp);
      gl.uniform1f(u.uTime, env.time); gl.uniform1f(u.uWind, env.wind);
      gl.uniform3fv(u.uSunDir, env.sunDir); gl.uniform3fv(u.uSunCol, env.sunCol); gl.uniform3fv(u.uSky, env.sky); gl.uniform3fv(u.uGround, env.ground);
      gl.uniform3fv(u.uCam, cam.eye); gl.uniform3fv(u.uFog, env.fog); gl.uniform1f(u.uFogD, env.fogD); gl.uniform1f(u.uFogStart, Math.max(0, (cam.dist || 0) - 32)); gl.uniform1f(u.uExposure, env.exposure);
      gl.uniform1f(u.uShadowOn, shadows ? 1 : 0); gl.uniform2f(u.uTexel, 1 / this.shadowSize, 1 / this.shadowSize);
      gl.activeTexture(gl.TEXTURE0); gl.bindTexture(gl.TEXTURE_2D, this.shadowTex); gl.uniform1i(u.uShadow, 0);
      gl.uniform1i(u.uMap, 1);
      const opaque = this.sets.filter(s => s.visible && s.count && !s.mat.transparent);
      const trans = this.sets.filter(s => s.visible && s.count && s.mat.transparent).sort((a, b) => a.mat.order - b.mat.order);
      const drawSet = (s) => {
        const m = s.mat;
        gl.uniform1f(u.uSpec, m.spec); gl.uniform1f(u.uUnlit, m.unlit ? 1 : 0); gl.uniform1f(u.uAlphaMul, m.alpha);
        gl.activeTexture(gl.TEXTURE1); gl.bindTexture(gl.TEXTURE_2D, m.map ? m.map.tex : this.white.tex); gl.uniform1f(u.uUseMap, m.map ? 1 : 0);
        if (m.doubleSided) gl.disable(gl.CULL_FACE); else gl.enable(gl.CULL_FACE);
        gl.bindVertexArray(s.vao); gl.drawElementsInstanced(gl.TRIANGLES, s.mesh.count, s.mesh.itype, 0, s.count);
        this.stats.draws++; this.stats.tris += s.mesh.tris * s.count;
      };
      for (const s of opaque) drawSet(s);
      // lines
      gl.useProgram(this.progLine);
      gl.uniformMatrix4fv(this.u.line.uVP, false, cam.vp);
      gl.enable(gl.BLEND); gl.blendFunc(gl.SRC_ALPHA, gl.ONE_MINUS_SRC_ALPHA);
      for (const lb of this.lines) {
        if (!lb.visible || !lb.n) continue;
        gl.bindVertexArray(lb.vao);
        gl.bindBuffer(gl.ARRAY_BUFFER, lb.bP); gl.bufferSubData(gl.ARRAY_BUFFER, 0, lb.P, 0, lb.n * 3);
        gl.bindBuffer(gl.ARRAY_BUFFER, lb.bC); gl.bufferSubData(gl.ARRAY_BUFFER, 0, lb.C, 0, lb.n * 4);
        if (!lb.depthTest) gl.disable(gl.DEPTH_TEST);
        gl.depthMask(false); gl.drawArrays(gl.LINES, 0, lb.n); gl.depthMask(true);
        gl.enable(gl.DEPTH_TEST);
      }
      // transparent sets
      gl.useProgram(this.progLit);
      gl.depthMask(false);
      for (const s of trans) {
        if (s.mat.additive) gl.blendFunc(gl.SRC_ALPHA, gl.ONE); else gl.blendFunc(gl.SRC_ALPHA, gl.ONE_MINUS_SRC_ALPHA);
        drawSet(s);
      }
      // particles
      gl.useProgram(this.progPt);
      gl.uniformMatrix4fv(this.u.pt.uVP, false, cam.vp); gl.uniform3fv(this.u.pt.uCam, cam.eye); gl.uniform1f(this.u.pt.uScale, H * 0.5 / Math.tan(cam.fov / 2) * 0.01);
      for (const pb of this.points) {
        if (!pb.visible || !pb.n) continue;
        gl.blendFunc(gl.SRC_ALPHA, pb.additive ? gl.ONE : gl.ONE_MINUS_SRC_ALPHA);
        gl.bindVertexArray(pb.vao);
        gl.bindBuffer(gl.ARRAY_BUFFER, pb.bP); gl.bufferSubData(gl.ARRAY_BUFFER, 0, pb.P, 0, pb.n * 3);
        gl.bindBuffer(gl.ARRAY_BUFFER, pb.bC); gl.bufferSubData(gl.ARRAY_BUFFER, 0, pb.C, 0, pb.n * 4);
        gl.bindBuffer(gl.ARRAY_BUFFER, pb.bS); gl.bufferSubData(gl.ARRAY_BUFFER, 0, pb.S, 0, pb.n);
        gl.drawArrays(gl.POINTS, 0, pb.n);
      }
      gl.depthMask(true); gl.disable(gl.BLEND); gl.enable(gl.CULL_FACE);
      gl.bindVertexArray(null);
    }
  }

  // ── orbit camera with damping, touch and keyboard ──
  class OrbitCamera {
    constructor(canvas) {
      this.canvas = canvas;
      this.target = [0, 0, 0]; this.dist = 40; this.az = -0.75; this.el = 0.62; this.fov = 45 * Math.PI / 180;
      this.goal = { target: [0, 0, 0], dist: 40, az: -0.75, el: 0.62 };
      this.minDist = 4; this.maxDist = 140; this.follow = null; this.auto = 0;
      this.eye = [0, 0, 0]; this.vp = M4.ident(); this.view = M4.ident(); this.proj = M4.ident();
      this.shift = [0, 0]; // lens shift (NDC) so the orbit target sits in the middle of the unobstructed area
      this._ptrs = new Map(); this._moved = 0; this.onClick = null;
      const el = canvas;
      el.addEventListener('pointerdown', e => { el.setPointerCapture(e.pointerId); this._ptrs.set(e.pointerId, { x: e.clientX, y: e.clientY, sx: e.clientX, sy: e.clientY, btn: e.button }); this._moved = 0; this.auto = 0; });
      el.addEventListener('pointermove', e => {
        const p = this._ptrs.get(e.pointerId); if (!p) return;
        const dx = e.clientX - p.x, dy = e.clientY - p.y; p.x = e.clientX; p.y = e.clientY;
        this._moved += Math.abs(dx) + Math.abs(dy);
        if (this._ptrs.size === 2) {
          const [a, b] = [...this._ptrs.values()];
          const d = Math.hypot(a.x - b.x, a.y - b.y);
          if (this._pinch) { this.goal.dist = Math.min(this.maxDist, Math.max(this.minDist, this.goal.dist * this._pinch / Math.max(1, d))); }
          this._pinch = d;
          this._pan(dx * 0.5, dy * 0.5);
        } else if (p.btn === 2 || e.shiftKey || p.btn === 1) this._pan(dx, dy);
        else { this.goal.az -= dx * 0.006; this.goal.el = Math.min(1.52, Math.max(0.08, this.goal.el + dy * 0.005)); }
      });
      const up = e => { const p = this._ptrs.get(e.pointerId); this._ptrs.delete(e.pointerId); this._pinch = null; if (p && this._moved < 6 && this.onClick && e.button === 0) this.onClick(e); };
      el.addEventListener('pointerup', up); el.addEventListener('pointercancel', e => { this._ptrs.delete(e.pointerId); this._pinch = null; });
      el.addEventListener('wheel', e => { e.preventDefault(); this.goal.dist = Math.min(this.maxDist, Math.max(this.minDist, this.goal.dist * Math.exp(e.deltaY * 0.0012))); this.auto = 0; }, { passive: false });
      el.addEventListener('contextmenu', e => e.preventDefault());
      // PADDY: keyboard — arrows orbit and tilt, Shift+arrows or WASD pan, +/− zoom, PageUp/PageDown tilt
      el.addEventListener('keydown', e => { if (this.key(e)) { e.preventDefault(); this.auto = 0; } });
      this.instant = false; // PADDY: true = no damping (prefers-reduced-motion)
    }
    key(e) {
      const k = e.key, g = this.goal, step = e.altKey ? 0.02 : 0.08;
      if (k === '+' || k === '=') { g.dist = Math.max(this.minDist, g.dist * 0.88); return true; }
      if (k === '-' || k === '_') { g.dist = Math.min(this.maxDist, g.dist / 0.88); return true; }
      if (k === 'PageUp') { g.el = Math.min(1.52, g.el + step); return true; }
      if (k === 'PageDown') { g.el = Math.max(0.08, g.el - step); return true; }
      const pan = e.shiftKey || /^[wasdWASD]$/.test(k);
      const dir = { ArrowLeft: [-1, 0], ArrowRight: [1, 0], ArrowUp: [0, -1], ArrowDown: [0, 1], a: [-1, 0], d: [1, 0], w: [0, -1], s: [0, 1],
        A: [-1, 0], D: [1, 0], W: [0, -1], S: [0, 1] }[k];
      if (!dir) return false;
      if (pan) this._pan(dir[0] * 40, dir[1] * 40);
      else if (dir[0]) g.az -= dir[0] * step * 2;
      else g.el = Math.min(1.52, Math.max(0.08, g.el - dir[1] * step));
      return true;
    }
    _pan(dx, dy) {
      this.follow = null;
      const s = this.goal.dist * 0.0016;
      const cx = Math.cos(this.goal.az), sx = Math.sin(this.goal.az);
      // screen right = (cos az, 0, -sin az)… derived from the eye direction
      this.goal.target[0] += (-dx * cx - dy * sx) * s;
      this.goal.target[2] += (dx * sx - dy * cx) * s;
    }
    set(view, instant = false) { Object.assign(this.goal, JSON.parse(JSON.stringify(view))); if (instant) { this.target = this.goal.target.slice(); this.dist = this.goal.dist; this.az = this.goal.az; this.el = this.goal.el; } }
    update(dt, aspect, followPos) {
      if (this.auto) this.goal.az += this.auto * dt;
      if (followPos) { this.goal.target[0] = followPos[0]; this.goal.target[1] = followPos[1]; this.goal.target[2] = followPos[2]; }
      const k = this.instant ? 1 : 1 - Math.exp(-dt * 7);
      for (let i = 0; i < 3; i++) this.target[i] += (this.goal.target[i] - this.target[i]) * k;
      this.dist += (this.goal.dist - this.dist) * k; this.el += (this.goal.el - this.el) * k;
      let daz = this.goal.az - this.az; daz = Math.atan2(Math.sin(daz), Math.cos(daz)); this.az += daz * k;
      const ce = Math.cos(this.el);
      this.eye = [this.target[0] + this.dist * ce * Math.sin(this.az), this.target[1] + this.dist * Math.sin(this.el), this.target[2] + this.dist * ce * Math.cos(this.az)];
      this.view = M4.lookAt(this.eye, this.target, [0, 1, 0]);
      this.proj = M4.perspective(this.fov, aspect, 0.1, 600);
      this.proj[8] = -this.shift[0]; this.proj[9] = -this.shift[1];
      this.vp = M4.mul(this.proj, this.view);
      this.aspect = aspect;
    }
    // world-space ray through a pixel (css coords)
    ray(x, y) {
      const r = this.canvas.getBoundingClientRect();
      const nx = (x - r.left) / r.width * 2 - 1, ny = 1 - (y - r.top) / r.height * 2;
      const inv = M4.invert(this.vp);
      const p0 = this._unproj(inv, nx, ny, -1), p1 = this._unproj(inv, nx, ny, 1);
      const d = [p1[0] - p0[0], p1[1] - p0[1], p1[2] - p0[2]], l = Math.hypot(...d);
      return { o: p0, d: d.map(v => v / l) };
    }
    _unproj(inv, x, y, z) { const v = [x, y, z, 1], o = [0, 0, 0, 0]; for (let r = 0; r < 4; r++) o[r] = inv[r] * v[0] + inv[4 + r] * v[1] + inv[8 + r] * v[2] + inv[12 + r] * v[3]; return [o[0] / o[3], o[1] / o[3], o[2] / o[3]]; }
    // project world → css pixels (null if behind)
    project(p) {
      const m = this.vp, x = p[0], y = p[1], z = p[2];
      const cx = m[0] * x + m[4] * y + m[8] * z + m[12], cy = m[1] * x + m[5] * y + m[9] * z + m[13], cw = m[3] * x + m[7] * y + m[11] * z + m[15];
      if (cw <= 0.1) return null;
      const r = this.canvas.getBoundingClientRect();
      return { x: (cx / cw * 0.5 + 0.5) * r.width, y: (1 - (cy / cw * 0.5 + 0.5)) * r.height, depth: cw };
    }
  }

  return { M4, hexLin, mixLin, srgb, Engine, OrbitCamera };
})();
