/**
 * WebGL2 compositor: camera RGB + segmentation mask + background → output.
 * Includes temporal EMA on the mask for hair-friendly, Meet-like stability.
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
uniform float u_maskGamma;

float sampleMask(vec2 uv) {
  // Slight blur via 5-tap cross for edge refinement
  vec2 texel = vec2(1.0) / vec2(textureSize(u_mask, 0));
  float c = texture(u_mask, uv).r;
  float l = texture(u_mask, uv + vec2(-texel.x, 0.0)).r;
  float r = texture(u_mask, uv + vec2(texel.x, 0.0)).r;
  float u = texture(u_mask, uv + vec2(0.0, -texel.y)).r;
  float d = texture(u_mask, uv + vec2(0.0, texel.y)).r;
  float m = (c * 2.0 + l + r + u + d) / 6.0;
  m = pow(clamp(m, 0.0, 1.0), u_maskGamma);
  // Soften transition for hair / fine edges
  return smoothstep(0.5 - u_edgeSoftness, 0.5 + u_edgeSoftness, m);
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
    maskGamma: WebGLUniformLocation;
  };
  private width = 0;
  private height = 0;

  /** CPU-side previous mask for temporal EMA (Float32 grayscale) */
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
      maskGamma: loc("u_maskGamma"),
    };

    // Fullscreen quad
    const vao = gl.createVertexArray();
    if (!vao) throw new Error("Failed to create VAO");
    this.vao = vao;
    gl.bindVertexArray(vao);
    const buf = gl.createBuffer();
    gl.bindBuffer(gl.ARRAY_BUFFER, buf);
    // pos.xy, uv.xy — flip Y for video textures
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
   * Apply temporal EMA to a grayscale mask ImageData / Uint8ClampedArray (R channel used).
   * Returns a Uint8Array suitable for LUMINANCE upload.
   */
  smoothMask(
    maskData: Uint8Array | Uint8ClampedArray,
    width: number,
    height: number,
    temporalAlpha: number,
  ): Uint8Array {
    const n = width * height;
    if (!this.prevMask || this.maskW !== width || this.maskH !== height) {
      this.prevMask = new Float32Array(n);
      this.maskW = width;
      this.maskH = height;
      for (let i = 0; i < n; i++) {
        // MediaPipe category mask is often single-channel; support RGBA too
        const stride = maskData.length === n ? 1 : 4;
        this.prevMask[i] = maskData[i * stride]! / 255;
      }
    } else {
      const a = temporalAlpha;
      for (let i = 0; i < n; i++) {
        const stride = maskData.length === n ? 1 : 4;
        const cur = maskData[i * stride]! / 255;
        this.prevMask[i] = a * this.prevMask[i]! + (1 - a) * cur;
      }
    }
    const out = new Uint8Array(n);
    for (let i = 0; i < n; i++) {
      out[i] = Math.round(this.prevMask[i]! * 255);
    }
    return out;
  }

  draw(opts: {
    camera: TexImageSource;
    mask: Uint8Array;
    maskWidth: number;
    maskHeight: number;
    background: BackgroundSourceLike;
    edgeSoftness?: number;
    maskGamma?: number;
  }) {
    const gl = this.gl;
    gl.useProgram(this.program);
    gl.bindVertexArray(this.vao);

    gl.activeTexture(gl.TEXTURE0);
    gl.bindTexture(gl.TEXTURE_2D, this.camTex);
    gl.texImage2D(gl.TEXTURE_2D, 0, gl.RGBA, gl.RGBA, gl.UNSIGNED_BYTE, opts.camera);
    gl.uniform1i(this.uniforms.camera, 0);

    gl.activeTexture(gl.TEXTURE1);
    gl.bindTexture(gl.TEXTURE_2D, this.maskTex);
    gl.pixelStorei(gl.UNPACK_ALIGNMENT, 1);
    gl.texImage2D(
      gl.TEXTURE_2D,
      0,
      gl.LUMINANCE,
      opts.maskWidth,
      opts.maskHeight,
      0,
      gl.LUMINANCE,
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
    gl.uniform1f(this.uniforms.edgeSoftness, opts.edgeSoftness ?? 0.12);
    gl.uniform1f(this.uniforms.maskGamma, opts.maskGamma ?? 0.85);

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

function hexToRgb(hex: string): [number, number, number] {
  const h = hex.replace("#", "");
  const full = h.length === 3 ? h.split("").map((c) => c + c).join("") : h;
  const n = parseInt(full, 16);
  return [((n >> 16) & 255) / 255, ((n >> 8) & 255) / 255, (n & 255) / 255];
}
