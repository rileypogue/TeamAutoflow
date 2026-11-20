from labjack import ljm


h = ljm.openS("T7", "ANY", "ANY")
try:
    ljm.eStreamStop(h) # clear any old streams
except ljm.LJMError:
    pass



names = ["AIN0", "AIN1", "AIN2", "AIN3"] 


addrs = ljm.namesToAddresses(len(names), names)[0]
rate = ljm.eStreamStart(h, 500, len(addrs), addrs, 1000)
print("actual scan rate: ", rate)

try:
    for i in range(10):
        data, dev_backlog, ljm_backlog = ljm.eStreamRead(h)
        print(i, len(data), [round(v, 3) for v in data [:4]], dev_backlog,ljm_backlog)
finally:
    ljm.eStreamStop(h)
    ljm.close(h)
