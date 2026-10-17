// @ts-nocheck (fixture uses an enum and dangling imports on purpose)

export function greet(name: string): string {
  return `hi ${name}`
}

export default class Greeter {
  hello() {
    return 1
  }
  static make() {
    return new Greeter()
  }
}

interface Shape {
  area(): number
}

export type Id = string

enum Color {
  Red,
  Green,
}

export const double = (n: number) => n * 2

export const fe = function () {
  return 0
}

const hidden = 5
