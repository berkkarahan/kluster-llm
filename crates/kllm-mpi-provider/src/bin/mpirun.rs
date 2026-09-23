//! The `mpirun` launcher binary (an alias for `mpiexec`). All logic lives in
//! [`mpi::launcher`].

fn main() {
    mpi::launcher::run();
}
