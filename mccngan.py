#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Created on Fri May 11 00:35:05 2018

@author: pankaj
"""

"""
Main code for a GAN training session.
"""
import datetime
import os
import re
import torch.utils.data
import torchvision
from collections import defaultdict
from tensorboardX import SummaryWriter
from torch.autograd import Variable
from torch.optim import lr_scheduler, Adam
from settings import Settings
import transforms
import viewer
from crowd_dataset import CrowdDataset
from hardware import gpu, cpu
from model import Generator, Discriminator1, load_trainer, save_trainer
#from settings import Settings
camera_count =5
image_count =5

settings = Settings()
settings.train_dataset_path = '/media/pankaj/D04BA26478DAA96B/PANKAJ/latest/5 Cameras 5 Images/test'
settings.batch_size = 400
settings.trial_name = 'CNN {} Cameras {} Images lr {:e}'.format(camera_count, image_count, settings.learning_rate)

def clean_scientific_notation(string):
    regex = r'\.?0*e([+\-])0*([0-9])'
    string = re.sub(regex, r'e\g<1>\g<2>', string)
    string = re.sub(r'e\+', r'e', string)
    return string


settings.trial_name = clean_scientific_notation(settings.trial_name)



################################### Do image transformation ############################################

train_transform = torchvision.transforms.Compose([transforms.RandomlySelectPatchAndRescale(),
                                                  transforms.RandomHorizontalFlip(),
                                                  transforms.NegativeOneToOneNormalizeImage(),
                                                  transforms.NumpyArraysToTorchTensors()])
validation_transform = torchvision.transforms.Compose([transforms.RandomlySelectPatchAndRescale(),
                                                       transforms.NegativeOneToOneNormalizeImage(),
                                                       transforms.NumpyArraysToTorchTensors()])

################################## Load Data from  path ##################################################

train_dataset = CrowdDataset(settings.train_dataset_path, 'train', transform=train_transform)
train_dataset_loader = torch.utils.data.DataLoader(train_dataset, batch_size=settings.batch_size, shuffle=True,num_workers=settings.number_of_data_loader_workers)
validation_dataset = CrowdDataset(settings.validation_dataset_path, 'validation', transform=validation_transform)
validation_dataset_loader = torch.utils.data.DataLoader(validation_dataset, batch_size=settings.batch_size, shuffle=False, num_workers=settings.number_of_data_loader_workers)


################################## Create Objects of models ###################################################
generator = Generator()
discriminator1 = Discriminator1()
gpu(generator)
gpu(discriminator1)

################################## Create Optimizer objects ##################################################
generator_optimizer = Adam(generator.parameters())
discriminator_optimizer = Adam(discriminator1.parameters())

################ Initialize Variables###########################
step = 0
epoch = 0

###################################### Loading for Discriminator #####################################

if settings.load_model_path:
    d_model_state_dict, d_optimizer_state_dict, epoch, step = load_trainer(prefix='discriminator1')
    discriminator1.load_state_dict(d_model_state_dict)
    discriminator_optimizer.load_state_dict(d_optimizer_state_dict)
    
discriminator_optimizer.param_groups[0].update({'lr': settings.initial_learning_rate,'weight_decay': settings.weight_decay})
discriminator_scheduler = lr_scheduler.LambdaLR(discriminator_optimizer,lr_lambda=settings.learning_rate_multiplier_function)
discriminator_scheduler.step(epoch)

########################################  Loading for Generator  #################################################################
if settings.load_model_path:
    g_model_state_dict, g_optimizer_state_dict, _, _ = load_trainer(prefix='generator')
    generator.load_state_dict(g_model_state_dict)
    generator_optimizer.load_state_dict(g_optimizer_state_dict)

generator_optimizer.param_groups[0].update({'lr': settings.initial_learning_rate})
generator_scheduler = lr_scheduler.LambdaLR(generator_optimizer, lr_lambda=settings.learning_rate_multiplier_function)
generator_scheduler.step(epoch)

#####################################  Code for Tensorboard  ##########################################
running_scalars = defaultdict(float)
validation_running_scalars = defaultdict(float)

running_example_count = 0

########### Creating directory  Log path , summary writer for train and validation #################
datetime_string = datetime.datetime.now().strftime("y%Ym%md%dh%Hm%Ms%S")
trial_directory = os.path.join(settings.log_directory, settings.trial_name + ' ' + datetime_string)
os.makedirs(trial_directory, exist_ok=True)
summary_writer = SummaryWriter(os.path.join(trial_directory, 'train'))
validation_summary_writer = SummaryWriter(os.path.join(trial_directory, 'validation'))

######################################### Start Training   ###############################################
print('Starting training...')
while epoch < settings.number_of_epochs:
    for examples in train_dataset_loader:

################# Real image discriminator processing #################################
        discriminator_optimizer.zero_grad()
        images, labels, _ = examples
        images, labels = Variable(gpu(images)), Variable(gpu(labels))
        predicted_labels, predicted_counts = discriminator1(images) # discriminator (input = real image = images)

        density_loss = torch.abs(predicted_labels - labels).pow(settings.loss_order).sum(1).sum(1).mean()
        count_loss = torch.abs(predicted_counts - labels.sum(1).sum(1)).pow(settings.loss_order).mean()
        loss = count_loss + (density_loss * 10)
        
        loss.backward()

################# Fake image discriminator processing #################################
        current_batch_size = images.data.shape[0]
        z = torch.randn(current_batch_size, 100)
        fake_images = generator(Variable(gpu(z)))# generator (input = random z )
        fake_predicted_labels, fake_predicted_counts = discriminator1(fake_images) # discriminator (input = fake images)

        fake_density_loss = torch.abs(fake_predicted_labels).pow(settings.loss_order).sum(1).sum(1).mean()
        fake_count_loss = torch.abs(fake_predicted_counts).pow(settings.loss_order).mean()
        fake_discriminator_loss = fake_count_loss + (fake_density_loss * 10)
        
        fake_discriminator_loss.backward(retain_graph=True)

#################################### Gradient penalty ##############################################
        alpha = Variable(gpu(torch.rand(current_batch_size, 1, 1, 1)))
        interpolates = alpha * images + ((1.0 - alpha) * fake_images)
        interpolates_labels, interpolates_counts = discriminator1(interpolates) # discriminator(input = interpolated (real images + fake images) )

        density_gradients = torch.autograd.grad(outputs=interpolates_labels, inputs=interpolates,grad_outputs=gpu(torch.ones(interpolates_labels.size())),create_graph=True, retain_graph=True, only_inputs=True)[0]
        density_gradients = density_gradients.view(current_batch_size, -1)
        density_gradient_penalty = ((density_gradients.norm(2, dim=1) - 1) ** 2).mean() * 10

        count_gradients = torch.autograd.grad(outputs=interpolates_counts, inputs=interpolates,grad_outputs=gpu(torch.ones(interpolates_counts.size())),create_graph=True, retain_graph=True, only_inputs=True)[0]
        count_gradients = count_gradients.view(current_batch_size, -1)
        count_gradients_penalty = ((count_gradients.norm(2, dim=1) - 1) ** 2).mean() * 10

        gradient_penalty = count_gradients_penalty + density_gradient_penalty * 10
        
        gradient_penalty.backward()

############# Discriminator update ############
        discriminator_optimizer.step()
######################################## Generator image processing #############################
        generator_optimizer.zero_grad()
        z = torch.randn(current_batch_size, 100)
        fake_images = generator(Variable(gpu(z))) # generator (input = random z)
        fake_predicted_labels, fake_predicted_counts = discriminator1(fake_images) # discriminator(input = fake images)

        generator_density_loss = fake_predicted_labels.sum(1).sum(1).mean()
        generator_count_loss = fake_predicted_counts.mean()
        generator_loss = (generator_count_loss + (generator_density_loss * 10)).neg()
############# Generator update ###############
        if step % 5 == 0:
            generator_loss.backward()
            generator_optimizer.step()
##################################### Tensorboard X ##############################################
        running_scalars['Loss'] += loss.data[0]
        running_scalars['Count Loss'] += count_loss.data[0]
        running_scalars['Density Loss'] += density_loss.data[0]
        running_scalars['Fake Discriminator Loss'] += fake_discriminator_loss.data[0]
        running_scalars['Generator Loss'] += generator_loss.data[0]

##################################################### Discriminator histogram ####################
        for name, param in discriminator1.named_parameters():
            summary_writer.add_histogram(name,param.clone().cpu().data.numpy(),global_step=step,bins='auto')

######################################## TensorboardX Comparision of images #################################
        running_example_count += images.size()[0]
        if step % settings.summary_step_period == 0 and step != 0:
            comparison_image = viewer.create_crowd_images_comparison_grid(cpu(images), cpu(labels),cpu(predicted_labels))
            summary_writer.add_image('Comparison', comparison_image, global_step=step)
            fake_images_image = torchvision.utils.make_grid(fake_images.data[:9], nrow=3)
            summary_writer.add_image('Fake', fake_images_image, global_step=step)

##################################### TensorboardX Scalars #########################################
            mean_loss = running_scalars['Loss'] / running_example_count
            print('[Epoch: {}, Step: {}] Loss: {:g}'.format(epoch, step, mean_loss))

            for name, running_scalar in running_scalars.items():
                mean_scalar = running_scalar / running_example_count
                summary_writer.add_scalar(name, mean_scalar, global_step=step)
                running_scalars[name] = 0
            running_example_count = 0
            
################################## Validation Code begins####################################################
            for validation_examples in validation_dataset_loader:
                images, labels, _ = validation_examples
                images, labels = Variable(gpu(images)), Variable(gpu(labels))
                predicted_labels, predicted_counts = discriminator1(images) # discriminator (images)
                
                density_loss = torch.abs(predicted_labels - labels).pow(settings.loss_order).sum(1).sum(1).mean()
                count_loss = torch.abs(predicted_counts - labels.sum(1).sum(1)).pow(settings.loss_order).mean()
                count_mae = torch.abs(predicted_counts - labels.sum(1).sum(1)).mean()
############################### Tensorboard X scalars #################################################
                validation_running_scalars['Density Loss'] += density_loss.data[0]
                validation_running_scalars['Count Loss'] += count_loss.data[0]
                validation_running_scalars['Count MAE'] += count_mae.data[0]
############################### Tensorboard comparision image ################################
            comparison_image = viewer.create_crowd_images_comparison_grid(cpu(images), cpu(labels),cpu(predicted_labels))
            validation_summary_writer.add_image('Comparison', comparison_image, global_step=step)

            for name, running_scalar in validation_running_scalars.items():
                mean_scalar = running_scalar / len(validation_dataset)
                validation_summary_writer.add_scalar(name, mean_scalar, global_step=step)
                validation_running_scalars[name] = 0
############################### 
        step += 1
    epoch += 1
    discriminator_scheduler.step(epoch)
    generator_scheduler.step(epoch)
#################################   Saving discriminator,generator  after training #############################
    if epoch != 0 and epoch % settings.save_epoch_period == 0:
        save_trainer(trial_directory, discriminator1, discriminator_optimizer, epoch, step, prefix='discriminator')
        save_trainer(trial_directory, generator, generator_optimizer, epoch, step, prefix='generator')
save_trainer(trial_directory, discriminator1, discriminator_optimizer, epoch, step, prefix='discriminator')
save_trainer(trial_directory, generator, generator_optimizer, epoch, step, prefix='generator')
print('epoch =',epoch)
print('Finished Training')
###############################
