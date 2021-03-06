import gluoncv
from mxnet import gluon , autograd
import numpy as np
from mxnet.gluon.data.vision import transforms
import mxnet as mx
import os
import argparse
from gluoncv.utils.parallel import *
from gluoncv.loss import MixSoftmaxCrossEntropyLoss
from segdataset import VOCSegmentation
import glob




parser = argparse.ArgumentParser(
    description="Training Script")
parser.add_argument("-c",
                    "--classes",
                    default="./",
                    help="List of classes(.txt file)",
                    type=str)

parser.add_argument("-p",
                    "--path_dataset",
                    help="Path to dataset",
                    default="./",
                    type=str)

parser.add_argument("-b",
                    "--batch",
                    help="Batch Size",
                    default=12,
                    type=int)

parser.add_argument("-lr",
                    "--l_rate",
                    default=0.001,
                    help="Learning rate",
                    type=float)

parser.add_argument("-w",
                    "--w_decay",
                    default=0.0001,
                    help="Weight decay",
                    type=float)

parser.add_argument("-e",
                    "--epochs",
                    default=50,
                    help="Number of epochs",
                    type=int)
parser.add_argument("-ch",
                    "--checkpoint",
                    default="./",
                    help="Path to checkpoint directory",
                    type=str)

args = parser.parse_args()





def get_classes():
    f = open(args.classes , "r")
    classes = f.readlines()
    classes = [c.rstrip() for c in classes]
    f.close()
    return classes
    

# List of all classes in the dataset
c = get_classes()

# Transforms for Normalization
input_transform = transforms.Compose([
    transforms.ToTensor(),
    transforms.Normalize([.485, .456, .406], [.229, .224, .225]),
])

# Create Dataset
trainset = VOCSegmentation(root=args.path_dataset ,split='train',
                            cls=c , transform=input_transform)

# Create Training Loader
train_data = gluon.data.DataLoader(
    trainset, args.batch , shuffle=True, last_batch='rollover',
    num_workers=4)



#Loading Fully Convolutional Network with a pretrained base Resnet101

model = gluoncv.model_zoo.FCN(nclass=len(c), backbone='resnet101', height=256, width=256 , ctx=mx.gpu(0))
model.hybridize()


# Loss function to be used (Can be changed based on problem statement)
criterion = MixSoftmaxCrossEntropyLoss(aux=True)

#Scheduler for training
lr_scheduler = gluoncv.utils.LRScheduler('poly', base_lr=args.l_rate,
                                         nepochs=args.epochs, iters_per_epoch=len(train_data), power=0.9)


#Passing model to dataparallel model so that we can run it on GPU

ctx_list = [mx.gpu(0)]
model = DataParallelModel(model, ctx_list)
criterion = DataParallelCriterion(criterion, ctx_list)

# SGD SOLVER
kv = mx.kv.create('device')
optimizer = gluon.Trainer(model.module.collect_params(), 'sgd',
                          {'lr_scheduler': lr_scheduler,
                           'wd':args.w_decay,
                           'momentum': 0.9,
                           'multi_precision': True},
                          kvstore = kv)

print(model)

def get_latest_checkpoint():
    files = [os.path.basename(x) for x in glob.glob(args.checkpoint+"/*.params")]
    if len(files) == 0:
        return None
    else :
        maxep=0
        for f in files :
            ep = f.split("_")
            ep_no = int(ep[1].split(".")[0])
            if ep_no >= maxep :
                maxep = ep_no
        maxep_file = 'epoch_%04d.params' % (maxep)
        return os.path.join(args.checkpoint ,maxep_file)

load_params = get_latest_checkpoint()
if load_params is not None:
    model.module.load_parameters(load_params, ctx=ctx_list)



#Function to save checkpoints i.e .params file of model.
def save_checkpoint(net, save_dir, epoch, is_best=False):
    """Save Checkpoints of model
       Parameters
       ------------------
       net : model
       save_dir : path to the directory for saving model
       epoch : Number of epoch for which we are saving the params.
    """
    
    filename = 'epoch_%04d.params' % (epoch)
    filepath = os.path.join(save_dir, filename)
    net.module.save_parameters(filepath)
    
                
epoch = args.epochs
batch_size=args.batch
for x in range(epoch+1):
    train_loss = 0.0
    for i, (data, target) in enumerate(train_data):
        with autograd.record(True):
            outputs = model(data)

            losses = criterion(outputs, target)
            mx.nd.waitall()
            autograd.backward(losses)
        optimizer.step(batch_size)
        for loss in losses:
            train_loss += np.mean(loss.asnumpy())/ len(losses)
        print('Epoch %d, batch %d, training loss %.3f'%(x, i, train_loss/(i+1)))
    if x%10==0:
        print("Saving parameters for epoch : ",x)
        save_checkpoint(model ,args.checkpoint , x)




