/* MIPS O32 ABI _start for non-PIC static binary */
    .section .text
    .global _start
    .set noreorder
    .set nomips16

_start:
    /* Setup global pointer for accessing globals */
    .option pic0
    lui     $gp, %hi(_gp)
    addiu   $gp, $gp, %lo(_gp)

    /* Ensure stack is 8-byte aligned */
    li      $t0, ~7
    and     $sp, $sp, $t0

    /* Call our C entry function */
    .extern ar8035_main
    la      $t9, ar8035_main
    jalr    $t9
    nop

    /* sys_exit($v0) */
    move    $a0, $v0
    li      $v0, 4001
    syscall
    nop
