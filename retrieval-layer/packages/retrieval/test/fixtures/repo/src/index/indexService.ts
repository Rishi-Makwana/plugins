import { walkFiles } from './walker.ts'

export class IndexService {
  private readonly root: string
  private indexedCount = 0

  constructor(root: string) {
    this.root = root
  }

  async run(): Promise<number> {
    for (const file of walkFiles(this.root)) {
      this.indexedCount += file.length > 0 ? 1 : 0
    }
    return this.indexedCount
  }
}
