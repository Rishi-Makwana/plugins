import type { Embedder } from './types.ts'

export const DEFAULT_MODEL = 'Xenova/bge-small-en-v1.5'
export const DEFAULT_DIMS = 384
// Documented by BAAI for bge v1.5 retrieval: queries get this prefix, passages get none.
export const BGE_QUERY_PREFIX = 'Represent this sentence for searching relevant passages: '

export interface TransformersEmbedderOptions {
  model?: string
  // Electron passes app.getPath('userData')/models so downloads land outside the asar.
  cacheDir?: string
  // true (default) = int8 model_quantized.onnx; false = fp32.
  quantized?: boolean
  // Only needed for models other than the default; verified against the first output.
  dims?: number
  queryPrefix?: string
}

export function createTransformersEmbedder(opts: TransformersEmbedderOptions = {}): Embedder {
  const model = opts.model ?? DEFAULT_MODEL
  const dims = opts.dims ?? DEFAULT_DIMS
  const isBge = model.toLowerCase().includes('bge')
  const queryPrefix = opts.queryPrefix ?? (isBge ? BGE_QUERY_PREFIX : '')
  const quantized = opts.quantized ?? true

  type Extractor = (texts: string[], options: { pooling: 'cls' | 'mean'; normalize: boolean }) => Promise<{ data: Float32Array; dims: number[] }>
  let loading: Promise<Extractor> | undefined
  const load = (): Promise<Extractor> => {
    // Lazy and cached: importing the runtime (onnxruntime-node) only happens when an embedding is first requested.
    loading ??= import('@huggingface/transformers').then(
      (lib) =>
        lib.pipeline('feature-extraction', model, {
          dtype: quantized ? 'q8' : 'fp32',
          ...(opts.cacheDir ? { cache_dir: opts.cacheDir } : {}),
        }) as unknown as Promise<Extractor>,
    )
    loading.catch(() => (loading = undefined))
    return loading
  }

  return {
    id: `${model}${quantized ? ':q8' : ':fp32'}`,
    dims,
    async embed(texts, kind) {
      if (texts.length === 0) return []
      const extractor = await load()
      const inputs = kind === 'query' ? texts.map((text) => queryPrefix + text) : texts
      const output = await extractor(inputs, { pooling: isBge ? 'cls' : 'mean', normalize: true })
      const width = output.dims[output.dims.length - 1]!
      if (width !== dims) throw new Error(`embedder ${model}: model produced ${width} dims, expected ${dims}; pass the dims option`)
      return texts.map((_, i) => output.data.slice(i * width, (i + 1) * width))
    },
  }
}
