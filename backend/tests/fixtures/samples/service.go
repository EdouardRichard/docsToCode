package main

import (
	"fmt"
)

// User represents a system user.
type User struct {
	ID    int
	Name  string
	Email string
}

// UserService handles user-related business logic.
type UserService struct {
	users map[int]*User
}

// FindUser retrieves a user by ID.
func (s *UserService) FindUser(id int) (*User, error) {
	u, ok := s.users[id]
	if !ok {
		return nil, fmt.Errorf("user %d not found", id)
	}
	return u, nil
}

// ProcessData is a standalone function that processes input data.
func ProcessData(data []byte) (int, error) {
	return len(data), nil
}

// Reader is an interface for reading data.
type Reader interface {
	Read(p []byte) (n int, err error)
}
