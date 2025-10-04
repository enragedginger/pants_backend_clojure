package com.enragedginger.java_project;

import org.junit.Test;
import static org.junit.Assert.assertEquals;

public class SomeJavaTest {
    @Test
    public void testAdder() {
        SomeJava someJava = new SomeJava();
        assertEquals(5, someJava.adder(2, 3));
    }
}
