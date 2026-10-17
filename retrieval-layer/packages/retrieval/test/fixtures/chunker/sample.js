const fs = require('fs')

function load(p) {
  return fs.readFileSync(p)
}

class Repo {
  get(id) {
    return id
  }
}

export const save = (x) => x
