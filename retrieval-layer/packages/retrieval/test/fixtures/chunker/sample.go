package main

import "fmt"

func Hello() {
	fmt.Println("hi")
}

type Server struct {
	Port int
}

func (s *Server) Start() {
}

func (s Server) Stop() {
}

type (
	A int
	B string
)
