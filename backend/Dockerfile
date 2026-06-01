FROM golang:1.26 AS builder

WORKDIR /app

COPY go.mod go.sum ./
RUN go mod download

COPY . .

RUN CGO_ENABLED=0 GOOS=linux go build -o airport-search .

FROM alpine:latest

WORKDIR /root/

COPY --from=builder /app/airport-search .

EXPOSE 8080

CMD ["./airport-search"]