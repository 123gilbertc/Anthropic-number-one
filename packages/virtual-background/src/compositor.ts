/**
 * WebGL2 compositor: camera RGB + person mask + background → output.
 * Mask convention: 1.0 = person (keep camera), 0.0 = background (show virtual set).
 */

const VERT = `#version 300 es
in vec2 a_pos;
in vec2 a_uv;
out vec2 v_uv;
void main() {
  v_uv = a_uv;
  gl_Position = vec4(a_pos, 0.0, 1.0);
}`;

const FRAG = `#version 300 es
precision highp float;
in vec2 v_uv;
out vec4 outColor;

uniform sampler2D u_camera;
uniform sampler2D u_mask;
uniform sampler2D u_bg;
uniform vec3 u_bgColor;
uniform int u_bgMode; // 0=none, 1=color, 2=image/video
uniform float u_edgeSoftness;
uniform float u_personBias;

float sampleMask(vec2 uv) {
  // 9-tap blur for hair / fine edges without eating into the subject
  vec2 texel = 1.0 / vec2(textureSize(u_mask, 0));
  float m = 0.0;
  m += texture(u_mask, uv + vec2(-texel.x, -texel.y)).r * 0.0625;
  m += texture(u_mask, uv + vec2(0.0, -texel.y)).r * 0.125;
  m += texture(u_mask, uv + vec2(texel.x, -texel.y)).r * 0.0625;
  m += texture(u_mask, uv + vec2(-texel.x, 0.0)).r * 0.125;
  m += texture(u_mask, uv).r * 0.25;
  m += texture(u_mask, uv + vec2(texel.x, 0.0)).r * 0.125;
  m += texture(u_mask, uv + vec2(-texel.x, texel.y)).r * 0.0625;
  m += texture(u_mask, uv + vec2(0.0, texel.y)).r * 0.125;
  m += texture(u_mask, uv + vec2(texel.x, texel.y)).r * 0.0625;

  // Bias threshold toward keeping the person (reduces BG "cutting into" face)
  float low = 0.5 - u_edgeSoftness - u_personBias;
  float high = 0.5 + u_edgeSoftness - u_personBias * 0.35;
  return smoothstep(low, high, clamp(m, 0.0, 1.0));
}

void main() {
  vec4 cam = texture(u_camera, v_uv);
  if (u_bgMode == 0) {
    outColor = cam;
    return;
  }
  float alpha = sampleMask(v_uv);
  vec3 bg;
  if (u_bgMode == 1) {
    bg = u_bgColor;
  } else {
    bg = texture(u_bg, v_uv).rgb;
  }
  // alpha=1 → person/camera in front; alpha=0 → virtual background behind
  outColor = vec4(mix(bg, cam.rgb, alpha), 1.0);
}`;

function compile(gl: WebGL2RenderingContext, type: number, src: string): WebGLShader {
  const shader = gl.createShader(type);
  if (!shader) throw new Error("Failed to create shader");
  gl.shaderSource(shader, src);
  gl.compileShader(shader);
  if (!gl.getShaderParameter(shader, gl.COMPILE_STATUS)) {
    const info = gl.getShaderInfoLog(shader);
    gl.deleteShader(shader);
    throw new Error(`Shader compile failed: ${info}`);
  }
  return shader;
}

export class WebGLCompositor {
  readonly canvas: HTMLCanvasElement;
  private gl: WebGL2RenderingContext;
  private program: WebGLProgram;
  private vao: WebGLVertexArrayObject;
  private camTex: WebGLTexture;
  private maskTex: WebGLTexture;
  private bgTex: WebGLTexture;
  private uniforms: {
    camera: WebGLUniformLocation;
    mask: WebGLUniformLocation;
    bg: WebGLUniformLocation;
    bgColor: WebGLUniformLocation;
    bgMode: WebGLUniformLocation;
    edgeSoftness: WebGLUniformLocation;
    personBias: WebGLUniformLocation;
  };
  private width = 0;
  private height = 0;

  /** CPU-side previous mask for temporal EMA (person alpha 0–1) */
  private prevMask: Float32Array | null = null;
  private maskW = 0;
  private maskH = 0;

  constructor(canvas?: HTMLCanvasElement) {
    this.canvas = canvas ?? document.createElement("canvas");
    const gl = this.canvas.getContext("webgl2", {
      premultipliedAlpha: false,
      alpha: false,
      antialias: false,
      powerPreference: "high-performance",
    });
    if (!gl) throw new Error("WebGL2 is required for AngleCast virtual backgrounds");
    this.gl = gl;

    const vs = compile(gl, gl.VERTEX_SHADER, VERT);
    const fs = compile(gl, gl.FRAGMENT_SHADER, FRAG);
    const program = gl.createProgram();
    if (!program) throw new Error("Failed to create program");
    gl.attachShader(program, vs);
    gl.attachShader(program, fs);
    gl.linkProgram(program);
    if (!gl.getProgramParameter(program, gl.LINK_STATUS)) {
      throw new Error(`Program link failed: ${gl.getProgramInfoLog(program)}`);
    }
    this.program = program;

    const loc = (name: string) => {
      const u = gl.getUniformLocation(program, name);
      if (!u) throw new Error(`Missing uniform ${name}`);
      return u;
    };
    this.uniforms = {
      camera: loc("u_camera"),
      mask: loc("u_mask"),
      bg: loc("u_bg"),
      bgColor: loc("u_bgColor"),
      bgMode: loc("u_bgMode"),
      edgeSoftness: loc("u_edgeSoftness"),
      personBias: loc("u_personBias"),
    };

    const vao = gl.createVertexArray();
    if (!vao) throw new Error("Failed to create VAO");
    this.vao = vao;
    gl.bindVertexArray(vao);
    const buf = gl.createBuffer();
    gl.bindBuffer(gl.ARRAY_BUFFER, buf);
    // Match HTML top-left origin: flip V so video + mask share the same orientation
    const quad = new Float32Array([
      -1, -1, 0, 1, 1, -1, 1, 1, -1, 1, 0, 0, -1, 1, 0, 0, 1, -1, 1, 1, 1, 1, 1, 0,
    ]);
    gl.bufferData(gl.ARRAY_BUFFER, quad, gl.STATIC_DRAW);
    const aPos = gl.getAttribLocation(program, "a_pos");
    const aUv = gl.getAttribLocation(program, "a_uv");
    gl.enableVertexAttribArray(aPos);
    gl.vertexAttribPointer(aPos, 2, gl.FLOAT, false, 16, 0);
    gl.enableVertexAttribArray(aUv);
    gl.vertexAttribPointer(aUv, 2, gl.FLOAT, false, 16, 8);

    this.camTex = this.createTexture();
    this.maskTex = this.createTexture();
    this.bgTex = this.createTexture();
  }

  private createTexture(): WebGLTexture {
    const gl = this.gl;
    const tex = gl.createTexture();
    if (!tex) throw new Error("Failed to create texture");
    gl.bindTexture(gl.TEXTURE_2D, tex);
    gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_WRAP_S, gl.CLAMP_TO_EDGE);
    gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_WRAP_T, gl.CLAMP_TO_EDGE);
    gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MIN_FILTER, gl.LINEAR);
    gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MAG_FILTER, gl.LINEAR);
    return tex;
  }

  resize(width: number, height: number) {
    if (width === this.width && height === this.height) return;
    this.width = width;
    this.height = height;
    this.canvas.width = width;
    this.canvas.height = height;
    this.gl.viewport(0, 0, width, height);
  }

  /**
   * Temporal smooth with person-preserving + motion-adaptive behavior.
   * Fast motion → trust the new mask (stops black BG tearing through the subject).
   * Still areas → light EMA for stable hair edges.
   */
  smoothMask(
    maskData: Uint8Array | Uint8ClampedArray,
    width: number,
    height: number,
    temporalAlpha: number,
  ): Uint8Array {
    const n = width * height;
    const stride = maskData.length === n ? 1 : 4;
    const cur = new Float32Array(n);
    for (let i = 0; i < n; i++) {
      cur[i] = (maskData[i * stride] ?? 0) / 255;
    }

    if (!this.prevMask || this.maskW !== width || this.maskH !== height) {
      this.prevMask = cur;
      this.maskW = width;
      this.maskH = height;
      return toBytes(cur);
    }

    // Motion amount: how much the person mask changed this frame
    let motion = 0;
    for (let i = 0; i < n; i += 4) {
      motion += Math.abs(cur[i]! - this.prevMask[i]!);
    }
    motion /= n / 4;
    // Map motion → blend toward current frame (0 = sticky, 1 = instant)
    const motionBoost = Math.min(1, motion * 6);
    // Base hold from quality preset, reduced when moving
    const hold = temporalAlpha * (1 - motionBoost * 0.85);
    const out = new Float32Array(n);

    for (let i = 0; i < n; i++) {
      const c = cur[i]!;
      const p = this.prevMask[i]!;
      let v = hold * p + (1 - hold) * c;
      // Asymmetric: prefer keeping person pixels (prevents BG eating into face/body)
      if (c > p) {
        v = Math.max(v, c * 0.85 + p * 0.15);
      }
      out[i] = v;
    }

    this.prevMask = out;
    return toBytes(out);
  }

  draw(opts: {
    camera: TexImageSource;
    mask: Uint8Array;
    maskWidth: number;
    maskHeight: number;
    background: BackgroundSourceLike;
    edgeSoftness?: number;
    personBias?: number;
  }) {
    const gl = this.gl;
    gl.useProgram(this.program);
    gl.bindVertexArray(this.vao);

    // Keep HTML and typed-array uploads in the same orientation
    gl.pixelStorei(gl.UNPACK_FLIP_Y_WEBGL, false);
    gl.pixelStorei(gl.UNPACK_ALIGNMENT, 1);
    gl.pixelStorei(gl.UNPACK_PREMULTIPLY_ALPHA_WEBGL, false);

    gl.activeTexture(gl.TEXTURE0);
    gl.bindTexture(gl.TEXTURE_2D, this.camTex);
    gl.texImage2D(gl.TEXTURE_2D, 0, gl.RGBA, gl.RGBA, gl.UNSIGNED_BYTE, opts.camera);
    gl.uniform1i(this.uniforms.camera, 0);

    gl.activeTexture(gl.TEXTURE1);
    gl.bindTexture(gl.TEXTURE_2D, this.maskTex);
    // WebGL2 R8 — LUMINANCE is unreliable across browsers
    gl.texImage2D(
      gl.TEXTURE_2D,
      0,
      gl.R8,
      opts.maskWidth,
      opts.maskHeight,
      0,
      gl.RED,
      gl.UNSIGNED_BYTE,
      opts.mask,
    );
    gl.uniform1i(this.uniforms.mask, 1);

    let mode = 0;
    let color: [number, number, number] = [0, 0, 0];
    if (opts.background.kind === "color") {
      mode = 1;
      color = hexToRgb(opts.background.color);
    } else if (opts.background.kind === "image" || opts.background.kind === "video") {
      mode = 2;
      gl.activeTexture(gl.TEXTURE2);
      gl.bindTexture(gl.TEXTURE_2D, this.bgTex);
      gl.texImage2D(gl.TEXTURE_2D, 0, gl.RGBA, gl.RGBA, gl.UNSIGNED_BYTE, opts.background.element);
      gl.uniform1i(this.uniforms.bg, 2);
    }

    gl.uniform1i(this.uniforms.bgMode, mode);
    gl.uniform3f(this.uniforms.bgColor, color[0], color[1], color[2]);
    gl.uniform1f(this.uniforms.edgeSoftness, opts.edgeSoftness ?? 0.1);
    gl.uniform1f(this.uniforms.personBias, opts.personBias ?? 0.08);

    gl.drawArrays(gl.TRIANGLES, 0, 6);
  }

  destroy() {
    const gl = this.gl;
    gl.deleteTexture(this.camTex);
    gl.deleteTexture(this.maskTex);
    gl.deleteTexture(this.bgTex);
    gl.deleteProgram(this.program);
    gl.deleteVertexArray(this.vao);
    this.prevMask = null;
  }
}

export type BackgroundSourceLike =
  | { kind: "none" }
  | { kind: "color"; color: string }
  | { kind: "image"; element: TexImageSource }
  | { kind: "video"; element: TexImageSource };

function toBytes(src: Float32Array): Uint8Array {
  const out = new Uint8Array(src.length);
  for (let i = 0; i < src.length; i++) {
    out[i] = Math.round(Math.min(1, Math.max(0, src[i]!)) * 255);
  }
  return out;
}

function hexToRgb(hex: string): [number, number, number] {
  const h = hex.replace("#", "");
  const full = h.length === 3 ? h.split("").map((c) => c + c).join("") : h;
  const n = parseInt(full, 16);
  return [((n >> 16) & 255) / 255, ((n >> 8) & 255) / 255, (n & 255) / 255];
}
