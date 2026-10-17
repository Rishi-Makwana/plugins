import { readFileSync } from 'node:fs'

export interface User {
  id: number
  name: string
}

export type UserMap = Map<number, User>

type Handler = (u: User) => void

export function loadUser(id: number): User {
  const raw = readFileSync(`/users/${id}.json`, 'utf8')
  return JSON.parse(raw) as User
}

function normalize(name: string): string {
  return name.trim().toLowerCase()
}

export const greet = (u: User): string => {
  return `hello ${normalize(u.name)}`
}

export const double = (n: number) => n * 2

const notExported = 5

export class UserStore {
  private users: UserMap = new Map()

  constructor(private readonly handler: Handler) {}

  add(u: User): void {
    this.users.set(u.id, u)
    this.handler(u)
  }

  get(id: number): User | undefined {
    return this.users.get(id)
  }

  async load(id: number): Promise<User> {
    const u = loadUser(id)
    this.add(u)
    return u
  }

  static create(handler: Handler): UserStore {
    return new UserStore(handler)
  }

  remove(id: number): boolean {
    return this.users.delete(id)
  }

  list(): User[] {
    return [...this.users.values()]
  }
}

export default class Other {
  run() {
    return 1
  }
}

export async function main(): Promise<void> {
  const store = UserStore.create((u) => console.log(greet(u)))
  await store.load(1)
  store.remove(1)
  for (const u of store.list()) {
    console.log(u)
  }
}
