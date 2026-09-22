/* Typecheck stub for the KLLM_HAVE_MPI loops. Not a real MPI library. */
#pragma once

typedef int MPI_Comm;
typedef int MPI_Datatype;
typedef int MPI_Op;

#define MPI_COMM_WORLD 0
#define MPI_BYTE 1
#define MPI_FLOAT 2
#define MPI_INT 3
#define MPI_SUM 4
#define MPI_MAX 5
#define MPI_SUCCESS 0

int MPI_Comm_rank(MPI_Comm comm, int* rank);
int MPI_Comm_size(MPI_Comm comm, int* size);
int MPI_Bcast(void* buffer, int count, MPI_Datatype datatype, int root, MPI_Comm comm);
int MPI_Allgather(const void* sendbuf, int sendcount, MPI_Datatype sendtype, void* recvbuf,
                  int recvcount, MPI_Datatype recvtype, MPI_Comm comm);
int MPI_Allgatherv(const void* sendbuf, int sendcount, MPI_Datatype sendtype, void* recvbuf,
                   const int* recvcounts, const int* displs, MPI_Datatype recvtype, MPI_Comm comm);
int MPI_Allreduce(const void* sendbuf, void* recvbuf, int count, MPI_Datatype datatype, MPI_Op op,
                  MPI_Comm comm);
