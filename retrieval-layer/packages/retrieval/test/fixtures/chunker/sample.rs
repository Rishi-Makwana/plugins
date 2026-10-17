use std::fmt;

pub fn run() {}

struct Point {
    x: i32,
}

enum Mode {
    A,
}

trait Draw {
    fn draw(&self);
}

type Alias = i32;

impl<T> Point<T> {
    fn new() -> Self {
        Point { x: 0 }
    }
}
